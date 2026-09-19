# agy-cli-manager

[English](README.md) | [中文](README.zh-CN.md)

`agy-cli-manager` 是一个用 Python 写的 Antigravity CLI（`agy`）多账号管理器，提供主备切换、配额感知的故障转移，以及可供程序调用的自动化接口。

它帮你更省心地使用多个 Antigravity CLI 账号：

- 从配额不足或已失效的账号上自动切走
- 监听 Antigravity CLI 日志中的 `Individual quota reached`，自动完成故障转移
- 让托管的运行时 profile 始终与当前激活账号保持一致
- 为机器人、调度器和外部程序暴露 CLI 与 Python API
- 支持手动或自动两种轮换策略

本仓库是 [zcop/agy-cli-manager](https://github.com/zcop/agy-cli-manager) 的 fork，增加了对**把凭证存放在系统钥匙串**（而非 token 文件）的 `agy` 版本的支持，详见[凭证存储](#凭证存储)。

设计上同一时刻只有一个账号处于激活状态：

- 保存多个 `agy` profile
- 显式切换，或在失败后自动切换
- 对外暴露机器可读的状态
- 既能当 CLI 工具、TUI 面板，也能当 Python 库

它与具体应用无关。Telegram 机器人可以调用它，但管理器本身并不绑定 Telegram。

## 功能

- 安全地保存多个账号 profile
- 保持一个账号激活，其余处于待命 / 冷却 / 停用状态
- 支持隔离的交互式 `agy` 登录
- 可导入已有的 `~/.gemini` 等实时目录
- 同时支持 token 文件式和系统钥匙串式的 `agy`（Linux Secret Service、macOS Keychain）
- 支持「仅手动」和「自动故障转移」两种切换模式
- 自动切换时优先挑选配额更充足、更健康的待命账号
- 跟踪缓存的身份、健康度和用量元数据
- 跟踪切换协调器的实时状态，便于调用方等待故障转移完成
- 提供 CLI 命令与 JSON 输出，便于自动化
- 带冷却时间的账号故障转移，状态变更受文件锁保护
- 跟踪 Antigravity CLI 日志，让运行中的 `agy` TUI 无需外部调用方即可触发故障转移

## 环境要求

- Python 3.10+
- `PATH` 中可用的 `agy` 可执行文件，或通过 `--agy-binary` 显式指定
- 若要使用 `login` 或全屏面板，需要一个终端
- Linux 上，如果你的 `agy` 把凭证存在系统钥匙串里，需要 `secret-tool`（`libsecret` 包）

## 凭证存储

较早的 `agy` 版本把 OAuth 凭证保存在文件里（`~/.gemini/antigravity-cli/antigravity-oauth-token`）。较新的版本改为存入系统原生的凭证存储：

| 平台 | 存储 | 槽位 |
| --- | --- | --- |
| Linux | Secret Service（D-Bus） | `service=gemini`、`username=antigravity` |
| macOS | Keychain | `service=gemini`、`account=antigravity` |

钥匙串里存的就是原来 token 文件中的同一份 JSON 文档，因此管理器仍以 token 文件作为磁盘上的 profile 格式，只在两个接缝处做桥接：保存账号时**从钥匙串取出**凭证，切换账号时**写回钥匙串**。

目录结构没有任何变化，profile 仍然放在 `~/.agy-cli-manager/accounts/<名称>/` 下。

切走之前，管理器会先把当前的实时凭证回存到即将离开的账号里 —— 因为 `agy` 在运行期间会就地刷新 token，不这么做的话，下次切回来会拿到过期的 refresh token。

`status` 会报告检测到的后端：

```
credential_store: secret-service (gemini/antigravity)
```

如果在 Linux 上显示 `none`，安装 libsecret：

```bash
sudo pacman -S libsecret        # Arch / Omarchy
sudo apt install secret-tools   # Debian / Ubuntu
```

以守护进程方式运行的 `watch` 需要能访问会话总线；当 `DBUS_SESSION_BUS_ADDRESS` 未设置时，管理器会回落到 `/run/user/$UID/bus`。

环境变量覆盖项：`AGY_CREDENTIAL_BACKEND`（`auto` | `secret-service` | `keychain` | `none`）、`AGY_KEYRING_SERVICE`、`AGY_KEYRING_USERNAME`。

**切换之后必须重启 `agy`** —— 运行中的进程已经把凭证缓存在内存里了。

## 平台支持

| 平台 | 凭证存储 | 状态 |
| --- | --- | --- |
| Linux（X11/Wayland 桌面） | 通过 `secret-tool` 使用 Secret Service | 支持，已实测 |
| macOS | 通过 `security` 使用 Keychain | 已实现，但 CI 未覆盖 |
| Linux（无头 / 容器） | 无 —— 仅 token 文件 | 若你的 `agy` 仍写 token 文件即可正常工作 |
| Windows | 无 —— 仅 token 文件 | 文件锁可用；未实现凭证管理器支持 |

除凭证存储外的一切都与平台无关：profile 存储、切换、冷却、日志监听以及 JSON API 在各平台上行为一致。CI 仅在 Linux 上运行。

在没有凭证存储后端的平台上，管理器会回落到 token 文件式 profile —— 这正是加入钥匙串支持之前的行为。对较早的 `agy` 版本来说没有问题；但如果你的 `agy` 把凭证存在系统钥匙串里，而管理器报告 `credential_store: none`，那么保存和切换账号将无法工作，直到有可用的后端为止。

## 安装

一行搞定，推荐用 [pipx](https://pipx.pypa.io)（环境隔离，并自动把 `agy-cli-manager` 放进 PATH）：

```bash
pipx install git+https://github.com/KaylaONeal/agy-cli-manager.git
```

没装 pipx 也可以用 venv：

```bash
python3 -m venv ~/.venvs/agy-cli-manager
~/.venvs/agy-cli-manager/bin/pip install git+https://github.com/KaylaONeal/agy-cli-manager.git
~/.venvs/agy-cli-manager/bin/agy-cli-manager --help
```

Linux 上，如果你的 `agy` 把凭证存在系统钥匙串里，还需要装 libsecret（见[凭证存储](#凭证存储)）：

```bash
sudo pacman -S libsecret        # Arch / Omarchy
sudo apt install secret-tools   # Debian / Ubuntu
```

验证是否成功：

```bash
agy-cli-manager status
```

只要 `credential_store:` 这一行显示出具体后端，就说明可以用了。

后续升级：

```bash
pipx upgrade agy-cli-manager
```

<details>
<summary>其他安装方式</summary>

从本地克隆以 editable 模式安装（开发用）：

```bash
git clone https://github.com/KaylaONeal/agy-cli-manager.git
cd agy-cli-manager
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

从上游的 release wheel 安装 —— 注意这些 wheel **尚未包含**本 fork 的钥匙串支持：

```bash
pip install https://github.com/zcop/agy-cli-manager/releases/download/v0.2.1/agy_cli_manager-0.2.1-py3-none-any.whl
```

完全不安装直接跑：

```bash
PYTHONPATH=src python3 -m agy_cli_manager.cli --help
```

</details>

## 快速开始

### 1. 初始化管理器状态

```bash
agy-cli-manager init
```

默认状态目录：

```text
~/.agy-cli-manager
```

可用 `--root /path/to/root` 覆盖。

### 2. 添加第一个账号

如果你已经有一个在用的 Antigravity 目录：

```bash
agy-cli-manager import-current my-account
```

在使用系统钥匙串的 `agy` 上，不需要再传目录参数 —— 凭证已经与 `~/.gemini` 无关了。如果你的 `agy` 仍使用 token 文件，则显式指定：

```bash
agy-cli-manager import-current my-account ~/.gemini
```

如果想让管理器直接驱动一次全新的交互式登录：

```bash
agy-cli-manager login my-account --agy-binary /path/to/agy
```

`login` 会把终端交给真正的 `agy` 会话。在那里完成正常的 Antigravity 登录流程，然后退出 `agy`，管理器会保存生成的 profile 快照。

在钥匙串模式下，`login` 会先把当前账号的凭证回存，再清空实时槽位 —— 否则 `agy` 会直接复用当前账号而不提示重新登录。

### 3. 查看当前状态

```bash
agy-cli-manager status
agy-cli-manager current
agy-cli-manager list
```

### 4. 打开面板

```bash
agy-cli-manager
```

不带子命令时默认打开全屏面板。

### 5. 配额用尽时自动切换

当 `agy` 触发 **Individual quota reached** 时，管理器会切到下一个账号。之后需要重启 `agy`。旧进程可能仍在写配额错误日志，这些行不会再次触发轮换，直到你确认重启、或出现新的会话日志。

![配额用尽？切换账号。](docs/quota-log-watch.svg)

```bash
agy-cli-manager switch-mode auto
agy-cli-manager watch
```

重启 `agy` 之后：

```bash
agy-cli-manager ack-restart
```

如果你更习惯用面板，可以让面板开着而不跑 `watch`（按 `Y` 确认重启）。除非你有意重放旧的配额错误，否则不要加 `--from-start`。

可以用 `--on-rotate` 在切换成功后执行命令：

```bash
agy-cli-manager watch --on-rotate 'notify-send "agy 已切换账号" "请重启 agy"'
```

## 常用命令

```bash
agy-cli-manager status --json
agy-cli-manager whoami
agy-cli-manager models --json
agy-cli-manager ensure-active --json
agy-cli-manager switch-mode
agy-cli-manager switch-mode manual
agy-cli-manager switch-mode auto
agy-cli-manager switch-policy --json
agy-cli-manager switch-policy --short-threshold 10 --refresh-failure-threshold 2 --candidate-strategy balanced
agy-cli-manager refresh-usage --json
agy-cli-manager switch-next
agy-cli-manager rotate-after-failure --reason quota --cooldown-minutes 60 --json
agy-cli-manager watch
agy-cli-manager watch --once --json
agy-cli-manager ack-restart
```

当前的切换策略保存在管理器状态里，可通过两种方式控制：

- CLI：`switch-mode`、`switch-policy`、`ensure-active`
- Python API：`get_status_snapshot()`、`get_switch_policy()`、`update_switch_policy()`、`ensure_active_account()`

目录结构：

```text
~/.agy-cli-manager/
├── accounts/
│   └── <账号名>/
│       └── .gemini/
│           └── ...
├── runtime/
│   └── .gemini/
└── state.json
```

可选集成：

- `live_dir` 可以指向真实的 Antigravity/Gemini CLI 目录，例如 `~/.gemini`
- 设置后，每次切换都会把托管的激活 profile 同步进这个实时目录

示例：

```bash
agy-cli-manager set-live-dir ~/.gemini
agy-cli-manager apply-active
```

当由其他进程启动 `agy`、而你希望那个实时目录始终反映当前激活的 profile 时，这很有用。

## 两种自动切换路径

**事后触发（`watch`）** —— 跟踪 `agy` 日志，匹配到配额耗尽的横幅后才切换。适合常驻运行。

**主动阈值（`ensure-active`）** —— 由 `refresh-usage` 拉取配额数据，按 `short_usage_threshold_percent`（默认剩余 10%）提前切换，不必等到报错。它不是常驻的，需要你用 cron 或 systemd timer 定时调用。

两条路径都不会自己重启 `agy`，请用 `--on-rotate` 或 `ack-restart` 配合。

## 面向 JSON / API 的用法

用于自动化时，优先使用支持 JSON 的命令：

```bash
agy-cli-manager status --json
agy-cli-manager current --json
agy-cli-manager list --json
agy-cli-manager ensure-active --json
agy-cli-manager switch-policy --json
```

完整的命令清单与 Python API 说明见[英文 README](README.md#jsonapi-oriented-usage)。

## 故障排查

### `Profile source is missing required auth files`

```text
error: Profile source is missing required auth files: /home/you/.gemini
```

说明你的 `agy` 把凭证存在系统钥匙串里，而不是 `~/.gemini/antigravity-cli/antigravity-oauth-token`。管理器本应自动读取钥匙串，出现这个错误意味着它没能访问到。

先看检测到的后端：

```bash
agy-cli-manager status | grep credential_store
```

- Linux 上显示 `credential_store: none` —— 安装 `libsecret`（见[凭证存储](#凭证存储)），并确认你是在桌面会话内运行，以便存在 D-Bus 会话总线。
- 显示 `credential_store: secret-service (...)` 但仍然失败 —— 说明槽位是空的。先用 `agy` 登录，然后确认：

  ```bash
  secret-tool lookup service gemini username antigravity | wc -c
  ```

  输出几百字节以上就说明凭证在那里。

后端正常后就不需要再传源目录了：

```bash
agy-cli-manager import-current my-account
```

### 切换了却没有效果

`agy` 会把凭证缓存在内存里，运行中的进程仍然使用旧账号。每次切换后都要重启 `agy`，然后：

```bash
agy-cli-manager ack-restart
```

### `watch` 看到了配额错误却不轮换

```text
watch: quota error observed; waiting for agy restart
```

这是刻意设计的。一次轮换之后，监听器会置上「待重启」标记，在你用 `ack-restart` 确认（或在面板里按 `Y`）之前不会再次轮换 —— 这样可以避免一个卡住的 `agy` 进程把所有账号挨个烧掉。

同时请确认你期望切换到的账号没有处于上一次轮换留下的冷却期：

```bash
agy-cli-manager status
```

### systemd 下的 `watch` 访问不到钥匙串

守护进程通常继承不到 `DBUS_SESSION_BUS_ADDRESS`。管理器会回落到 `/run/user/$UID/bus`，这能覆盖常见情况；如果你的环境不同，请在 unit 里显式设置：

```ini
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
```

### 配额数字不对，或自动切换从不触发

不同的 `agy` 版本连接的 Cloud Code 后端不一样，**同一个账号在不同后端上的配额可能不同**。管理器会查询 `DEFAULT_CODE_ASSIST_BASE_URLS` 里的每个后端，取**最紧张**的那个值 —— 因为高报剩余量会导致故障转移永远不触发。

配额还分成多个池子，「Gemini Models」可能是 100%，而「Claude and GPT models」已经耗尽。`refresh-usage --json` 会同时给出汇总值和分池明细：

```bash
agy-cli-manager refresh-usage --json | jq '.quota_groups, .quota_sources'
```

如果你清楚自己的 `agy` 用哪个后端，可以用 `AGY_CODE_ASSIST_BASE_URL` 固定（多个用逗号分隔）。

另外注意配额可能是**跨账号共享**的（家庭组，或按设备计）。如果两个账号的 `resetTime` 精确到秒都一样，说明它们用的是同一个池子，在它们之间轮换不会有任何帮助。

### status 出现 `credential_drift: WARNING`

说明系统钥匙串里的凭证不是当前激活账号的。`agy` 跟随钥匙串而不是管理器状态，所以此时 `agy` 实际用的账号和 `status` 显示的不是同一个。通常发生在直接跑 `agy` 登录、或有第二个管理器实例之后。重新发布激活账号即可：

```bash
agy-cli-manager switch <激活账号名>
```

### 运行测试

```bash
python3 -m pytest tests/
```

测试不会碰到真实的系统凭证存储：`tests/conftest.py` 会为每个测试强制关闭后端。新增测试时请保留这一机制 —— 那个实时槽位是和你真正在用的 `agy` 共享的。
