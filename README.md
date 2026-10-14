# ADB Query MCP

独立的 Python stdio MCP，固定一个 Android USB / 网络目标，提供 **1 个白名单命令执行工具和 1 个历史输出读取工具**。基于本目录开发计划完成 P0–P3；宿主任务工具选择、命令证据入库和原 case 端到端验收属于 P4。

当前接口（2026-10-14 核对）：

| Tool | 输入 | 输出 |
|---|---|---|
| `run_adb_shell` | 必填完整指令字符串 `command` | 正常结果直接返回 stdout 原文；错误、stderr 或截断另有提示 |
| `read_command_output` | 必填记录 ID；流、位置和长度可省略 | 返回已保存输出的文本窗口；有后续内容时附下一次调用参数 |

通常只需调用 `run_adb_shell`。传入与配置白名单逐字符相同的完整指令；设备地址由配置固定，不随调用切换。无需解析结果正文中的 JSON。完整参数和示例见下文“当前工具输入输出”。

## 安装与启动

Windows / PowerShell，Python **3.12**，只使用 uv 安装流程。所有操作在本项目目录执行；不借用或修改宿主虚拟环境。

```powershell
uv venv --python 3.12 .venv
uv pip sync --python .venv/Scripts/python.exe --require-hashes requirements.lock
uv pip install --python .venv/Scripts/python.exe --no-deps --no-build-isolation -e .
Copy-Item -LiteralPath config.example.json -Destination config.json
# 编辑 config.json：替换 <TV_IP>，核对 adb_path。
.venv/Scripts/python.exe -m adb_mcp --config config.json --check-config
.venv/Scripts/python.exe -m adb_mcp --config config.json
# 可安装入口等价：.venv/Scripts/adb-query-mcp.exe --config config.json
```

`--config` 必填；`--check-config` 只校验配置，不连接设备。正常服务的 stdout 只用于 MCP 协议；配置诊断与日志写 stderr。宿主启动环境建议设置 `PYTHONUTF8=1`。

`requirements.lock` 包含开发工具和传递依赖的精确版本、SHA-256，安装时验证哈希。更新依赖后才重新生成：

```powershell
uv pip compile pyproject.toml --extra dev --generate-hashes --output-file requirements.lock
```

## 配置与固定目标

网络形式：`"target": {"host": "192.0.2.1", "port": 5555}`。USB 形式：`"target": {"serial": "<EXACT_USB_SERIAL>"}`。二者互斥，未知配置字段、字符串端口、布尔端口等明确拒绝。

| 配置 | 默认值 / 约束 |
|---|---|
| allowed_commands | 完整指令字符串列表；省略或空列表时不允许执行任何命令 |
| adb_path | `adb`，也可使用完整可执行文件路径 |
| auto_connect | `true`；仅网络目标缺席时执行一次 connect |
| connect_timeout_seconds | 10；设备列表、connect、再次检查、features 共用这一预算 |
| command_timeout_seconds | 30；一次 shell 查询的采集预算 |
| cleanup_timeout_seconds | 5；异常后的终止和管道收尾预算 |
| preview_max_chars_per_stream | 6000；stdout/stderr 各自独立的预览上限 |
| read_max_chars | 12000；单次分页请求的硬上限 |
| max_capture_bytes | 16777216；两条原始流合计的持久采集上限 |
| output_directory | `./data/command-outputs`；相对配置文件所在目录解析 |

带路径分隔符的相对 `adb_path`（包括 `./adb.exe` 和 `.\adb.exe`）相对配置文件解析；裸 `adb` 或 `adb.exe` 通过 PATH 查找。IPv6 网络目标使用 `[host]:port`。示例配置中的 `<TV_IP>` 是待替换占位值，不能直接运行。

每个 shell 查询始终使用 `adb -s <完整目标> shell -T -n <已引用的设备端命令字符串>`。精确目标必须在 `adb devices -l` 中唯一且状态为 `device`；其他设备数量不影响选定目标。USB 不 connect；offline / unauthorized / 重复目标条目明确报错。要求该目标支持 `shell_v2`；旧协议不执行查询，以避免伪造退出码或混合 stdout/stderr。

服务实例内，同一配置目标的调用串行执行。默认连接、查询与异常清理预算为 45 秒，宿主工具超时应大于该预算，并预留持久化及协议处理时间；并发调用还有锁等待时间。没有跨 MCP 锁或多设备队列。ADB 客户端可能自行启动未运行的 ADB daemon；服务不显式执行 `start-server` / `kill-server`，不修复其他服务。

## 当前工具输入输出

### run_adb_shell

只接受一个完整指令字符串。输入必须与 `config.json` 的 `allowed_commands` 某项逐字符相同，才会执行。匹配前不去空格、不改大小写、不解析或规范化；未匹配时不启动任何 ADB 进程，也不创建输出记录。

```json
{"command":"adb shell ifconfig"}
```

白名单在配置里填写，修改后重启 MCP。示例配置保留此前 10 项查询，可按需要增减，不需要修改 Python 代码：

```json
{
  "allowed_commands": [
    "adb shell getprop ro.com.google.clientidbase",
    "adb shell getprop ro.boot.serialno",
    "adb shell getprop ro.serialno",
    "adb shell getprop ro.oem.key1",
    "adb shell getprop ro.vendor.youtube.cert_scope",
    "adb shell getprop persist.sys.test_harness",
    "adb shell pm list features",
    "adb shell settings get secure android_id",
    "adb shell ifconfig",
    "adb shell ip addr"
  ]
}
```

上面是配置字段片段；仍需保留 `target`、ADB 路径和其他现有设置。

| 输入（白名单只有 `adb shell ifconfig` 时） | 结果 |
|---|---|
| `adb shell ifconfig` | 允许 |
| `adb shell ifconfig wlan0` | 拒绝 |
| `adb shell IFCONFIG` | 拒绝 |
| `adb  shell ifconfig` | 拒绝 |
| `adb shell ifconfig `（尾部空格） | 拒绝 |
| `adb shell ifconfig; reboot` | 拒绝 |

白名单项必须以字面量 `adb shell ` 开头，后跟设备端程序及参数。配置校验拒绝控制字符、未闭合引号、Shell 运算符和替换字符（`; | & < >`、反引号、`$`）；不支持管道、重定向、变量展开或调用方的 ADB 选项。包含空格的参数可用 POSIX 引号填写，匹配仍比较原始字符串。配置中的额外空格会原样保留，调用方也必须完全匹配。

只有命中后才解析预设指令；使用配置的 `adb_path` 和固定 `target`，逐参数 POSIX 引用生成设备端命令，本机使用 `create_subprocess_exec`，不启动本机 Shell。字面量 `adb` 不是调用方选择可执行文件的入口。

**白名单是执行授权，不是自动判定只读的策略。** 服务不再硬编码属性或一级命令名单；添加指令前自行确认副作用。示例 10 项仍为查询，但配置可以授权修改设备的指令，因此执行工具不声明 `readOnlyHint=true`；历史读取工具继续声明只读。

### 文本输出

正常、完整且无 stderr 或解码异常时，只返回一个文本块，内容等于 stdout 原文；不包裹 JSON，不 trim 或改写换行。成功但值为空时，文本为空且 `isError=false`，不冒充命令失败。

例如输入 `{"command":"adb shell getprop ro.com.google.clientidbase"}`，调用方看到的文本可以是（示意值，实际由设备返回）：

```text
example_client_id
```

MCP 仍使用协议结果封装文本。上述示例对应：

```json
{
  "content": [{"type": "text", "text": "example_client_id\n"}],
  "isError": false
}
```

正文是 `content[0].text`，不是含 stdout、时间等字段的 JSON 对象；没有 `structuredContent` 或 `outputSchema`。正常短结果不附记录 ID。调用方检查协议 `isError`，并保留其他文本块中的必要提示。

只有需要说明时才增加独立文本块：

- stderr 非空：追加 `[stderr]` 文本块，保留其原文，与 stdout 分开。
- 命令失败：`isError=true`，追加 `[adb_mcp]` 提示，含错误分类、退出码及简短说明；已取得的 stdout/stderr 仍保留。缺失参数或类型错误由 SDK 拒绝；不匹配白名单的 command 由服务端拒绝，不执行 ADB。
- 输出预览截断：标明对应流已截断，并给出 `read_command_output` 的完整补读参数；`start_char` 从已显示的 Unicode 码点数开始。
- 采集不完整或解码失败：明确标明 `capture_complete=false` / `decoding_errors=true`。补读只能取得已保存部分，不能恢复未采集的尾部。

命令非零退出时，第一块仍为已取得的 stdout；第二块在 stderr 非空时出现，错误说明位于独立的 `[adb_mcp]` 块，协议 `isError=true`。例如以下三块文本（模拟场景，不是当前设备验收结果）：

```text
partial
```

```text
[stderr]
remote error
```

```text
[adb_mcp]
error=command_failed; exit_code=7; ADB shell exited with a nonzero status
```

输入校验失败的错误文本由 SDK 生成，不保证使用上述前缀；同样以协议 `isError=true` 表示失败。成功时 stderr 非空只追加该流的文本，不单凭 stderr 判断失败。

补读提示示例（ID 为占位值）：

```text
[adb_mcp]
stdout_truncated=true
read_command_output {"output_id":"<32_HEX_RECORD_ID>","stream":"stdout","start_char":6000}
```

设备、argv、时间、退出码、字节数、记录 ID 和完整性等元数据继续保存到本地 `record.json`，内部记录格式仍为 `adb-command/v1`，新增可选 `command` 字段保存命中时的原始完整指令。只有需要补读时才将 ID 和位置送入调用上下文。成功执行只代表取得事实，不判定 TAM 预期或 PASS/FAIL；空属性值加退出码 0 是成功事实。

**接口变更**：旧 `query` 请求必须改为完整 `command` 字符串。客户端应重启 MCP 并重新发现、绑定工具。配置省略 `allowed_commands` 时默认拒绝全部命令，不回退到旧固定名单。其他额外字段不参与命令生成。返回仍是原文及必要提示，没有 structuredContent 或输出 Schema。

### read_command_output

读取已保存的 stdout 或 stderr，不执行 ADB。仅在需要补读时使用提示中的参数。

| 参数 | 类型 | 必填 | 默认值 / 约束 |
|---|---|---|---|
| `output_id` | string | 是 | 32 位小写十六进制 ID，匹配 `^[0-9a-f]{32}$`；不能传文件路径 |
| `stream` | string，enum | 否 | `stdout`；可选 `stdout` / `stderr` |
| `start_char` | integer | 否 | `0`；必须 ≥ 0，按 Unicode 码点计数 |
| `max_chars` | integer | 否 | `min(6000, read_max_chars)`；范围为 1 到配置的 `read_max_chars`，默认配置上限为 12000 |

只有 ID 必填。通常照着截断提示调用，无需自己计算位置；示例中的 ID 是占位值，须替换为提示中的实际 ID：

```json
{"output_id":"<32_HEX_RECORD_ID>","stream":"stdout","start_char":6000}
```

读取成功时，第一个文本块是请求窗口内的原文。还有后续文本时，独立提示块包含下一次调用参数，start_char 是当前窗口结束位置；最后一页不附分页元数据。保留 CR/LF，偏移按 UTF-8 解码后的 Unicode 码点计数，不是字节、UTF-16 单元、token 或行号。

例如读取记录 `0123456789abcdef0123456789abcdef` 的 stdout，从位置 6000 取到 12000，仍有后续内容时，返回的提示块为：

```text
[adb_mcp]
read_command_output {"output_id":"0123456789abcdef0123456789abcdef","stream":"stdout","start_char":12000}
```

该 ID 和位置均为示例。正常最后一页只有原文；若原命令存在错误、采集不完整或解码异常，最后一页仍会保留相应提示。

原命令失败、原采集不完整或解码异常仍明确提示。读取已保存的失败命令可以成功（`isError=false`，`source_error=...` 表示原命令错误）；非法 ID、读取越界、存储损坏等读取错误则标记 `isError=true`。偏移等于总字符数返回空文本；超过总字符数、负数、错误 stream 或超过读取上限均报错。

每次读取按当前配置白名单校验。新记录的完整 command 必须精确匹配，并与 argv 一致；旧记录没有 command 时按 argv 与当前白名单解析后的参数比较。不允许的记录报错，原文件保留；当前允许的旧记录跨重启可读，无需迁移。只接受记录 ID，不能传路径，也不会重放设备命令。

## 原始记录与生命周期

```text
data/command-outputs/<output_id>/
  stdout.bin       # 原始字节
  stderr.bin       # 原始字节
  record.json      # 命令结果与预览，提交后才可读取
```

执行时并发持续读取两条管道，原始输出流式落盘，不在内存中无限累积。分页增量解码且仅保留请求窗口；每页扫描记录以计算总字符数，不重新执行 ADB。目录内未提交元数据的残片不冒充成功记录。

超时、取消、采集失败、磁盘错误或超出合计上限会终止本机 ADB 并等待进程和管道收尾。取消保留已取得的输出及原始取消异常；stdio 调用方收到协议取消响应而非正常工具结果，已保存记录 ID 写入 stderr 诊断日志，可稍后读取。清理未确认时阻断本实例后续设备操作并返回 `cleanup_failed`，需人工检查残留进程后重启服务。终止本机 ADB 不证明设备端命令已停止，服务不自动重放查询。

元数据使用临时文件加原子替换。落盘失败时，工具以 `isError=true` 和 `storage_failed` 文本提示报错，不返回可用于补读的 ID；内部结果的 output_id 为 null。记录跨服务重启可读。历史不自动清理；仅在人工确认需要后，归档或删除 `output_directory` 下对应记录 ID 的整目录（两条原始流及元数据一起处理），不要只删除某一条流。运行账号需要目录创建/读写权限；继承操作系统权限，不额外开放访问。记录可能含设备信息或私有设置，应按项目证据保留策略保护；`data/`、真实 `config.json`、`.venv` 均忽略，不提交 Git。

## 验证

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/ruff.exe check src tests scripts
.venv/Scripts/ruff.exe format --check src tests scripts
# 授权按配置白名单进行真机验收（先核对每条指令的副作用）：
.venv/Scripts/python.exe scripts/verify_device.py --config config.json --report data/device-validation.json
```

模拟 ADB 使用真实子进程、双流、退出码和等待，覆盖策略、固定目标、网络连接、空值、非零退出、超时、取消、清理阻断、磁盘错误、输出上限、单行长文本、UTF-8 分页和 stdio SDK 初始化/发现/调用/重启读取。真机脚本还执行固定 `exit 7` 只读验收夹具，验证真实退出码通道；该夹具没有开放为 MCP 工具。

实际结果见 [验证报告](docs/validation-report.md)。宿主接入片段及 P4 交接见 [接入说明](docs/testing-agent-integration.md)。

## 官方依据

- [MCP Python SDK v1.30.0](https://github.com/modelcontextprotocol/python-sdk/tree/v1.30.0)：使用安装版本的公开 FastMCP、返回类型注解与 stdio 客户端，未依赖私有 SDK 成员。
- [MCP Tools 规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)：结构化结果、工具错误和注解；`readOnlyHint` 只是提示，策略在服务端实现。
- [Android ADB 文档](https://developer.android.com/tools/adb)：精确设备选择、网络连接、设备端 Shell 参数引用。
