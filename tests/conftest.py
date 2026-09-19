"""Shared test setup.

Also a safety net: the manager publishes credentials into the real OS
credential store (Linux Secret Service / macOS Keychain), which is shared with
the live `agy` install. Tests must never touch it, so the backend is forced off
for every test; tests that exercise credential-store behaviour opt back in by
monkeypatching `credstore` directly or by setting the variable themselves.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def _isolate_os_credential_store(monkeypatch):
    monkeypatch.setenv("AGY_CREDENTIAL_BACKEND", "none")
