# ADB Query MCP

独立的 Python stdio MCP，固定一个 Android USB / 网络目标，提供 **2 个只读查询工具**。基于本目录开发计划完成 P0–P3；宿主任务工具选择、命令证据入库和原 case 端到端验收属于 P4。

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
| adb_path | `adb`，也可使用完整可执行文件路径 |
| auto_connect | `true`；仅网络目标缺席时执行一次 connect |
| connect_timeout_seconds | 10；设备列表、connect、再次检查、features 共用这一预算 |
| command_timeout_seconds | 30；一次 shell 查询的采集预算 |
| cleanup_timeout_seconds | 5；异常后的终止和管道收尾预算 |
| preview_max_chars_per_stream | 6000；stdout/stderr 各自独立的预览上限 |
| read_max_chars | 12000；单次分页请求的硬上限 |
| max_capture_bytes | 16777216；两条原始流合计的持久采集上限 |
| output_directory | `./data/command-outputs`；相对配置文件所在目录解析 |

带目录的相对 `adb_path` 也相对配置文件解析；单独的 `adb` 通过 PATH 查找。IPv6 网络目标使用 `[host]:port`。示例配置中的 `<TV_IP>` 是待替换占位值，不能直接运行。

每个 shell 查询始终使用 `adb -s <完整目标> shell -T -n <已引用的命令字符串>`。精确目标必须在 `adb devices -l` 中唯一且状态为 `device`；其他设备数量不影响选定目标。USB 不 connect；offline / unauthorized / 重复目标条目明确报错。要求该目标支持 `shell_v2`；旧协议不执行查询，以避免伪造退出码或混合 stdout/stderr。

服务实例内，同一配置目标的调用串行执行。默认连接、查询与异常清理预算为 45 秒，宿主工具超时应大于该预算，并预留持久化及协议处理时间；并发调用还有锁等待时间。没有跨 MCP 锁或多设备队列。ADB 客户端可能自行启动未运行的 ADB daemon；服务不显式执行 `start-server` / `kill-server`，不修复其他服务。

## 工具和命令边界

### run_adb_shell

```json
{"argv":["getprop","ro.com.google.clientidbase"]}
```

```json
{"argv":["settings","get","global","device_name"]}
```

```json
{"argv":["dumpsys","display"]}
```

首版允许以下完整组合：

- `getprop` 或 `getprop <属性名>`；属性名使用字母、数字、点、下划线、短横线，不能以短横线开头。
- `settings get <global|system|secure> <键>`；非空键可含 Unicode、空格和单双引号，不能以短横线开头。
- `settings list <global|system|secure>`。
- `dumpsys display`，不接受其他服务或附加参数。

最多 32 个参数、合计 4096 个 Unicode 字符。禁止控制/格式/代理字符，以及 `; | & < > \` $ \\`。不接受 `sh -c`、`su`、写设置、安装、重启、管道、重定向、串联或任意脚本。规则集中在 `commands.py`；增加合法属性/键不增加工具，增加 dumpsys 服务需先确认其只读参数组合。

本机用 `create_subprocess_exec`，设备端命令用 POSIX 逐参数引用后作为一条字符串提交，避免把参数列表误当作设备端转义。使用非 PTY 模式保留两条流和原始行尾。

### 命令结果 adb-command/v1

| 字段 | 契约 |
|---|---|
| schema_version | 固定 `adb-command/v1` |
| output_id | 32 位小写十六进制记录 ID；无记录或落盘失败为 null，不是宿主 Artifact ID |
| target_serial / argv | 配置的精确目标及实际通过策略的设备端参数 |
| started_at / finished_at | UTC，ISO 8601，带 `+00:00`；覆盖连接准备到执行及异常收尾 |
| duration_ms | 同一阶段的单调时钟耗时；不包含排队及结果预览/元数据落盘 |
| exit_code | ADB 进程自然结束时的实际码；shell_v2 传回远端退出码；超时/取消/超限等未自然结束为 null |
| stdout / stderr | UTF-8 原文或各自有界预览；不 trim、不合并、不生成语义摘要 |
| stdout_bytes / stderr_bytes | 已采集并保存的原始字节数；超限后的尾部不保存 |
| stdout_truncated / stderr_truncated | 本次预览是否省略记录中后续文本 |
| capture_complete | 两条原始流是否完整采集；不代表命令成功或用例通过 |
| decoding_errors | 任一原始流是否含无效 UTF-8；显示形式采用替换字符，原始字节仍保存 |
| error_code / error_message | 成功为 null；失败为明确分类和简短说明，诊断不混入 stderr 证据 |

工具通过公开 SDK 的 `Annotated[CallToolResult, CommandResult]` 输出 Schema、结构化数据和 `isError`，文本块只包含一份精简 JSON。空属性值加退出码 0 是成功事实，工具不判定 TAM 预期或 PASS/FAIL。非零退出即工具错误，但完整采集的失败命令可以有 `capture_complete=true`。ADB 自身连接/传输失败也可能返回非零码，不能仅凭退出码断言设备端命令确已执行。

错误类别包含 `invalid_command`、`device_not_found`、`device_offline`、`device_unauthorized`、`device_ambiguous`、`device_not_ready`、`connect_failed`、`connect_timeout`、`shell_protocol_unsupported`、`adb_unavailable`、`adb_error`、`command_failed`、`command_timeout`、`cancelled`、`capture_limit`、`capture_failed`、`storage_failed`、`cleanup_failed`。

### read_command_output

```json
{"output_id":"<32_HEX_RECORD_ID>","stream":"stdout","start_char":0,"max_chars":12000}
```

偏移从 0 开始，按解码后 **Unicode 码点**计数（不是字节、UTF-16 单元、token 或行号）。保留 CR/LF；组合字符按各自码点计数。返回 `start_char`、`text`、`next_char`、`eof`、`total_chars`、原记录 `capture_complete` / `decoding_errors` / `error_code`。`next_char` 可直接用于下一页。

偏移等于总字符数时返回空文本和 `eof=true`；超过总字符数、负数、错误 stream、非法 ID、超过服务端读取上限均报错。只接受记录 ID，不能传路径。分页成功不覆盖原命令的错误或不完整状态；解码失败时不能用替换后的文本做精确证据比对。短输出直接使用第一次返回，不必额外读取。

## 原始记录与生命周期

```text
data/command-outputs/<output_id>/
  stdout.bin       # 原始字节
  stderr.bin       # 原始字节
  record.json      # 命令结果与预览，提交后才可读取
```

执行时并发持续读取两条管道，原始输出流式落盘，不在内存中无限累积。分页增量解码且仅保留请求窗口；每页扫描记录以计算总字符数，不重新执行 ADB。目录内未提交元数据的残片不冒充成功记录。

超时、取消、采集失败、磁盘错误或超出合计上限会终止本机 ADB 并等待进程和管道收尾。取消保留已取得的输出及原始取消异常；stdio 调用方收到协议取消响应而非正常工具结果，已保存记录 ID 写入 stderr 诊断日志，可稍后读取。清理未确认时阻断本实例后续设备操作并返回 `cleanup_failed`，需人工检查残留进程后重启服务。终止本机 ADB 不证明设备端命令已停止，服务不自动重放查询。

元数据使用临时文件加原子替换，落盘失败返回 `output_id=null` / `storage_failed`。记录跨服务重启可读。历史不自动清理；仅在人工确认需要后，归档或删除 `output_directory` 下对应记录 ID 的整目录（两条原始流及元数据一起处理），不要只删除某一条流。运行账号需要目录创建/读写权限；继承操作系统权限，不额外开放访问。记录可能含设备信息或私有设置，应按项目证据保留策略保护；`data/`、真实 `config.json`、`.venv` 均忽略，不提交 Git。

## 验证

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/ruff.exe check src tests scripts
.venv/Scripts/ruff.exe format --check src tests scripts
# 授权真机只读查询：
.venv/Scripts/python.exe scripts/verify_device.py --config config.json --report data/device-validation.json
```

模拟 ADB 使用真实子进程、双流、退出码和等待，覆盖策略、固定目标、网络连接、空值、非零退出、超时、取消、清理阻断、磁盘错误、输出上限、单行长文本、UTF-8 分页和 stdio SDK 初始化/发现/调用/重启读取。真机脚本还执行固定 `exit 7` 只读验收夹具，验证真实退出码通道；该夹具没有开放为 MCP 工具。

实际结果见 [验证报告](docs/validation-report.md)。宿主接入片段及 P4 交接见 [接入说明](docs/testing-agent-integration.md)。

## 官方依据

- [MCP Python SDK v1.30.0](https://github.com/modelcontextprotocol/python-sdk/tree/v1.30.0)：使用安装版本的公开 FastMCP、返回类型注解与 stdio 客户端，未依赖私有 SDK 成员。
- [MCP Tools 规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)：结构化结果、工具错误和注解；`readOnlyHint` 只是提示，策略在服务端实现。
- [Android ADB 文档](https://developer.android.com/tools/adb)：精确设备选择、网络连接、设备端 Shell 参数引用。
