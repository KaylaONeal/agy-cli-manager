from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agy_cli_manager import credstore, manager


TOKEN_DOC = {
    "token": {
        "access_token": "at-value",
        "token_type": "Bearer",
        "refresh_token": "rt-value",
        "expiry": "2099-01-01T00:00:00+00:00",
    },
    "auth_method": "consumer",
}


class FakeStore:
    """In-memory stand-in for the OS credential store."""

    def __init__(self, value: str | None = None, available: bool = True):
        self.value = value
        self.available = available
        self.writes: list[str] = []
        self.clears = 0

    def install(self, monkeypatch):
        monkeypatch.setattr(credstore, "is_available", lambda: self.available)
        monkeypatch.setattr(credstore, "read_active", self._read)
        monkeypatch.setattr(credstore, "write_active", self._write)
        monkeypatch.setattr(credstore, "clear_active", self._clear)
        return self

    def _read(self):
        return self.value

    def _write(self, value):
        self.value = value
        self.writes.append(value)

    def _clear(self):
        self.value = None
        self.clears += 1


@pytest.fixture
def paths(tmp_path):
    return manager.build_paths(tmp_path / "root")


def _token_text(overrides: dict | None = None) -> str:
    doc = json.loads(json.dumps(TOKEN_DOC))
    if overrides:
        doc["token"].update(overrides)
    return json.dumps(doc)


def _make_home(tmp_path: Path, name: str, with_token: bool = True) -> Path:
    home = tmp_path / name
    profile = home / ".gemini"
    profile.mkdir(parents=True)
    if with_token:
        manager._write_profile_token(profile, _token_text())
    return home


# --- credstore unit tests ---------------------------------------------------


def test_decode_passes_through_plain_text():
    assert credstore._decode('{"token": {}}') == '{"token": {}}'


def test_decode_strips_go_keyring_base64_prefix():
    raw = '{"token": {"access_token": "x"}}'
    tagged = credstore.GO_KEYRING_BASE64_PREFIX + base64.b64encode(raw.encode()).decode()
    assert credstore._decode(tagged) == raw


def test_decode_rejects_corrupt_base64():
    with pytest.raises(credstore.CredentialStoreError):
        credstore._decode(credstore.GO_KEYRING_BASE64_PREFIX + "!!!not base64!!!")


def test_backend_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("AGY_CREDENTIAL_BACKEND", "none")
    assert credstore.backend_name() == credstore.BACKEND_NONE
    assert credstore.is_available() is False
    assert credstore.read_active() is None


def test_unknown_backend_env_is_rejected(monkeypatch):
    monkeypatch.setenv("AGY_CREDENTIAL_BACKEND", "bogus")
    with pytest.raises(credstore.CredentialStoreError):
        credstore.backend_name()


def test_service_and_username_are_overridable(monkeypatch):
    assert credstore.keyring_service() == "gemini"
    assert credstore.keyring_username() == "antigravity"
    monkeypatch.setenv("AGY_KEYRING_SERVICE", "other")
    monkeypatch.setenv("AGY_KEYRING_USERNAME", "someone")
    assert credstore.keyring_service() == "other"
    assert credstore.keyring_username() == "someone"


# --- bridge helpers ---------------------------------------------------------


def test_capture_writes_token_file_with_owner_only_mode(tmp_path, monkeypatch):
    FakeStore(_token_text()).install(monkeypatch)
    profile = tmp_path / ".gemini"
    assert manager._credstore_capture_into_profile(profile) is True
    path = manager._profile_token_path(profile)
    assert json.loads(path.read_text())["token"]["access_token"] == "at-value"
    assert path.stat().st_mode & 0o777 == 0o600


def test_capture_is_a_noop_when_the_slot_is_empty(tmp_path, monkeypatch):
    FakeStore(None).install(monkeypatch)
    profile = tmp_path / ".gemini"
    assert manager._credstore_capture_into_profile(profile) is False
    assert not manager._profile_token_path(profile).exists()


def test_activate_publishes_the_profile_token(tmp_path, monkeypatch):
    store = FakeStore(None).install(monkeypatch)
    home = _make_home(tmp_path, "acct")
    assert manager._credstore_activate_from_profile(home / ".gemini") is True
    assert json.loads(store.value)["token"]["refresh_token"] == "rt-value"


def test_activated_context_restores_the_previous_credential(tmp_path, monkeypatch):
    store = FakeStore(_token_text({"access_token": "live"})).install(monkeypatch)
    home = _make_home(tmp_path, "other")
    manager._write_profile_token(home / ".gemini", _token_text({"access_token": "other"}))

    with manager._credstore_activated(home / ".gemini") as activated:
        assert activated is True
        assert json.loads(store.value)["token"]["access_token"] == "other"

    assert json.loads(store.value)["token"]["access_token"] == "live"


def test_activated_context_captures_refreshes_back_into_the_profile(tmp_path, monkeypatch):
    store = FakeStore(_token_text({"access_token": "live"})).install(monkeypatch)
    home = _make_home(tmp_path, "other")
    manager._write_profile_token(home / ".gemini", _token_text({"access_token": "other"}))

    with manager._credstore_activated(home / ".gemini"):
        # Simulate agy refreshing the token while it runs.
        store.value = _token_text({"access_token": "refreshed"})

    saved = json.loads(manager._profile_token_path(home / ".gemini").read_text())
    assert saved["token"]["access_token"] == "refreshed"
    assert json.loads(store.value)["token"]["access_token"] == "live"


def test_activated_context_is_inert_without_a_backend(tmp_path, monkeypatch):
    FakeStore(None, available=False).install(monkeypatch)
    home = _make_home(tmp_path, "acct")
    with manager._credstore_activated(home / ".gemini") as activated:
        assert activated is False


# --- import / switch behaviour ---------------------------------------------


def test_import_current_works_with_a_keyring_only_profile(tmp_path, paths, monkeypatch):
    """The issue-2 case: ~/.gemini exists but holds no token file."""
    FakeStore(_token_text()).install(monkeypatch)
    home = _make_home(tmp_path, "live", with_token=False)

    manager.import_current(paths, "acct-a", home / ".gemini")

    stored = manager._profile_token_path(manager.account_dir(paths, "acct-a") / ".gemini")
    assert json.loads(stored.read_text())["token"]["access_token"] == "at-value"


def test_import_current_error_names_the_credential_store(tmp_path, paths, monkeypatch):
    FakeStore(None).install(monkeypatch)
    home = _make_home(tmp_path, "live", with_token=False)
    with pytest.raises(ValueError, match="no credential is stored"):
        manager.import_current(paths, "acct-a", home / ".gemini")


def test_switch_publishes_the_target_credential(tmp_path, paths, monkeypatch):
    store = FakeStore(_token_text({"access_token": "a"})).install(monkeypatch)
    home_a = _make_home(tmp_path, "a", with_token=False)
    manager.import_current(paths, "acct-a", home_a / ".gemini")

    store.value = _token_text({"access_token": "b"})
    home_b = _make_home(tmp_path, "b", with_token=False)
    manager.import_current(paths, "acct-b", home_b / ".gemini")

    manager.switch_account(paths, "acct-a")
    assert json.loads(store.value)["token"]["access_token"] == "a"
    manager.switch_account(paths, "acct-b")
    assert json.loads(store.value)["token"]["access_token"] == "b"


def test_switch_snapshots_the_outgoing_refreshed_credential(tmp_path, paths, monkeypatch):
    store = FakeStore(_token_text({"access_token": "a"})).install(monkeypatch)
    manager.import_current(paths, "acct-a", _make_home(tmp_path, "a", with_token=False) / ".gemini")
    store.value = _token_text({"access_token": "b"})
    manager.import_current(paths, "acct-b", _make_home(tmp_path, "b", with_token=False) / ".gemini")

    manager.switch_account(paths, "acct-a")
    # agy refreshes acct-a's token in the live slot while it runs.
    store.value = _token_text({"access_token": "a-refreshed", "refresh_token": "rt-2"})
    manager.switch_account(paths, "acct-b")

    saved = json.loads(
        manager._profile_token_path(manager.account_dir(paths, "acct-a") / ".gemini").read_text()
    )
    assert saved["token"]["access_token"] == "a-refreshed"
    assert saved["token"]["refresh_token"] == "rt-2"

    manager.switch_account(paths, "acct-a")
    assert json.loads(store.value)["token"]["access_token"] == "a-refreshed"


def test_file_backend_behaviour_is_unchanged(tmp_path, paths, monkeypatch):
    """With no credential store, token-file profiles still work end to end."""
    FakeStore(None, available=False).install(monkeypatch)
    home = _make_home(tmp_path, "live", with_token=True)
    manager.import_current(paths, "acct-a", home / ".gemini")
    manager.switch_account(paths, "acct-a")
    snapshot = manager.get_status_snapshot(paths)
    assert snapshot["active"] == "acct-a"
