# 独立 ADB MCP 开发计划

日期：2026-10-13。本文可直接作为新项目的开发输入。以下接口、默认值和阶段划分为建议方案；当前实现和执行记录中的事实单独标注。

## 1. 目标与已确认需求

开发一个独立的本地 ADB MCP，为测试 Agent 提供后台查询能力，覆盖不同属性和不同查询命令，而不是为每个属性或 case 新增一个工具。

- 不在现有 ATV MCP 上开发；新项目独立配置、安装、运行和验证。
- 主要场景是 Android TV 后台数据读取，例如 `getprop`、`settings get` 和 `dumpsys`。
- 使用少量通用工具，控制工具 Schema 和命令输出占用的模型上下文。
- 返回真实命令输出和退出状态；完整记录可保存、追溯和再次读取。
- ADB MCP 提供事实，不负责 TAM 期望值、用例 PASS/FAIL 或测试 Agent 的规划规则。
- 当前项目的工具筛选和命令证据验收属于宿主改造，单独交付。

初版按一个配置目标设备设计，兼容网络目标或 USB serial。调用时不临时切换设备。

## 2. 现有系统事实与问题

当前宿主：`D:\MOKA\testing-react-agent`。

| 已验证事实 | 对新项目的影响 |
|---|---|
| 宿主通过 `mcp.json` 接入 stdio MCP，支持 command、args、cwd、env | 新服务采用 stdio，使用新项目自身虚拟环境的绝对解释器路径 |
| 当前宿主运行环境为 mcp 1.30.0、langchain-mcp-adapters 0.3.2、pydantic 2.14.1 | 新项目先以 MCP SDK 1.30.0 为接入基线，开发时核对对应版本公开 API |
| 当前 ATV 只有能力查询和操作队列两个工具 | 不复用或修改 ATV 源码，不把后台查询塞进其操作队列 |
| 当前宿主加载所有配置服务的工具，并将全部工具绑定到执行模型 | 独立 MCP 不自动减少上下文；需宿主另行实现任务级工具选择 |
| 当前 Act/Judge 提示词要求视觉观察，passed/failed 校验要求实际保存的截图 | 新 MCP 单独完成后，后台 case 仍不能直接端到端通过 |
| 当前公开消息块支持文本和截图 Artifact，工具文本会保存 | 文本已保存不等于已被认可为终态证据，需要命令证据契约 |
| 原报告首轮设备未找到、后续恢复；最终是 5 轮耗尽，第二步 skipped | 连接准备、输出取证和结束调用均需计入轮次，不把 BLOCKED 当属性不匹配 |
| 原计划只提到 TAM 确认的期望值，没有提供可执行的具体值 | 属性读取成功后，缺少期望值仍应报告缺失输入，不能猜测 |

当前工作区存在用户已有修改。本计划保存在工作区外，不修改现有仓库、配置或 Git 历史。

## 3. 交付范围

### 新 ADB MCP 项目负责

- Python 包、stdio 服务、可安装命令入口。
- 配置校验和固定目标设备连接。
- 通用后台命令执行及命令策略校验。
- stdout/stderr 收集、退出状态、超时与取消处理。
- 有界的默认返回、完整记录保存、分页读取。
- SDK 级 MCP 接入测试、模拟 ADB 测试和授权设备上的真实验证。
- README、示例配置、宿主接入片段、验证报告。

### 现有测试 Agent 后续负责

- 注册新服务，按任务绑定必要工具。
- 将命令结果导入宿主证据存储，关联当前任务。
- 支持命令输出作为终态证据，更新提示词、结果和报告展示。
- 保留跨任务查询结果，确保后续 Judge 能读取前一步实际输出。
- 检查必要输入和预算，完成原 case 的真实端到端运行。

### 初版不包含

任意脚本、交互式 Shell、管道/重定向、多命令串联、安装卸载、写入系统设置、root、设备重启、ADB server 重启、多设备调度、远程部署和工具搜索平台。后续确有用例要求时单独定义这些能力。

## 4. 技术选型与结构

- Python 3.12，项目独立 `.venv`；不借用或修改宿主环境。
- MCP 官方 Python SDK，接入基线 `mcp==1.30.0`；Pydantic 2 做配置、参数和输出校验。
- 使用该版本的公开 FastMCP API，不照搬最新版主分支中的其他大版本 API。
- `pyproject.toml` 管理包和入口，提供锁定依赖的文件；新项目只选择一种安装流程。
- 配置采用 JSON，明确配置文件路径；所有相对数据目录相对配置文件所在目录解析。
- 日志写 stderr，stdout 只承载 MCP 协议。

建议结构：

```text
adb-mcp/
  pyproject.toml
  requirements.lock
  README.md
  config.example.json
  src/adb_mcp/
    __init__.py
    __main__.py       # 命令入口
    server.py         # 工具注册与协议输出
    config.py         # 配置校验
    adb.py            # 连接、子进程及生命周期
    commands.py       # 命令策略与输入/执行结果类型
    outputs.py        # 持久记录和分页读取
  tests/
    test_commands.py
    test_adb.py
    test_outputs.py
    test_mcp.py
  docs/
    testing-agent-integration.md
```

目录是建议，最终按实际职责调整；不创建通用框架或大量浅层模型文件。

## 5. 工具设计：初版最多两个

### 5.1 `run_adb_shell`

参数：`argv: list[str]`。命令包括设备侧可执行程序，列表不能为空；限制参数数量、总长度和控制字符。超时和输出上限由服务配置统一决定，不为每次调用开放无约束参数。

示例：

```json
{"argv":["getprop","ro.com.google.clientidbase"]}
```

```json
{"argv":["settings","get","global","device_name"]}
```

```json
{"argv":["dumpsys","display"]}
```

短输出返回完整文本；长输出返回原文预览、长度和截断标记，不由服务擅自生成语义摘要。统一结果建议包含：

| 字段 | 含义 |
|---|---|
| schema_version | 命令结果契约版本，例如 adb-command/v1 |
| output_id | MCP 生成的记录标识，不是宿主 Artifact ID |
| target_serial | 本次实际选定的完整 ADB 目标标识 |
| argv | 通过校验后实际提交的命令参数 |
| started_at / finished_at | 带时区的时间字符串 |
| duration_ms | 从连接准备到执行收尾的耗时，单位毫秒 |
| exit_code | 正常结束时的真实退出码；未能确认时为 null |
| stdout / stderr | 本次返回的原文或原文预览，分别保存，不混合 |
| stdout_bytes / stderr_bytes | 实际已收集的原始字节数 |
| stdout_truncated / stderr_truncated | 返回内容是否省略后续文本 |
| capture_complete | 原始输出是否完整采集成功 |
| decoding_errors | 文本预览是否存在无法解码的原始字节 |
| error_code | 成功为 null；失败为明确的错误分类 |

使用 MCP 的结构化输出和标准 `isError` 表达执行状态。文本内容可放一次精简 JSON，供当前宿主适配器消费；验证模型实际收到的消息中没有重复灌入同一份结果。

命令非零退出、设备未连接、超时、输出硬上限、证据落盘失败均属于工具执行错误。`getprop` 退出码为零但值为空，是成功执行得到的业务事实；由 case 判定空值是否违反要求，工具不擅自写成 FAIL。

### 5.2 `read_command_output`

参数：`output_id`、`stream`（stdout/stderr）、`start_char`（从 0 开始）、`max_chars`。字符位置按 Unicode 码点计数，不是字节或 token；请求上限不能超过服务端 `read_max_chars`。保留原有换行，不按行裁剪，保证单行超长也能有界分页。短输出不要要求额外调用。

必须返回 `start_char`、实际文本 `text`、`next_char`、`eof`、总字符数，以及记录本身的完整性信息。到达末尾后的合法请求返回空文本和 eof；负偏移或超出记录范围的偏移明确报参数错误。只接受合法记录 ID，不接受任意文件路径。不重新执行原命令，不产生新的设备观测。

原始 stdout/stderr 按字节保存；分页文本只是查看形式。解码不完整时明确标记，不能假装逐字符比对已获完整证据。记录跨服务重启可读取。初版不自动清理历史；README 提供人工归档/清理范围，运行数据不进入 Git。

该工具用于已确认的 dumpsys 长输出需求；不是为每种查询新增工具。

## 6. 命令边界与设备连接

### 命令策略

初版覆盖：任意合法属性名的 `getprop`、`settings get` / `settings list`，以及明确校验的 `dumpsys` 查询调用。

- 命令校验在服务端，不能由 MCP readOnlyHint 或模型自述替代。
- 校验程序、子命令和参数组合；不是仅检查命令前缀。
- 特别检查 dumpsys 服务和可选参数：不默认认为所有服务子命令都只读。首批确定支持 `display` 的标准查询，其他服务按实际 case 增加规则。
- 命令规则扩展集中在一个模块，不改变 MCP 工具数量或复制工具 Schema。
- 不允许通过 `sh -c`、`su`、命令替换或串联语法绕过规则。
- 本机使用 `create_subprocess_exec`，不经过 PowerShell/cmd；ADB 的设备端仍存在 Shell 解析，必须正确逐参数引用并用真实设备验证，不把参数数组当作已经解决转义。
- 工具描述简明列出命令家族及限制，完整说明放 README；不要在 Schema 中列出所有属性或 dumpsys 服务。

### 目标与连接

配置明确区分 USB serial 与网络 host/port，二者互斥。USB serial 必须与 `adb devices -l` 中的标识一致；网络目标生成完整 `host:port`，不使用裸 IP 作为精确设备标识。

- 一次调用始终指定同一目标，不因列表中只有另一台设备而自动切换。
- 网络目标需要时可在执行前进行一次有界 `adb connect`；连接后检查精确目标为 device。
- USB 不调用 connect；offline、unauthorized、多设备歧义均返回明确错误。
- 相同配置设备的调用在服务实例内串行执行；初版无后台队列、多设备调度或跨 MCP 锁。
- 新服务返回实际目标供核对。它不能自动保证 ATV/TV-UITree 配置一致，接入验收需逐项核对。
- 不调用 `adb kill-server` / `start-server` 修复其他服务，不自动重放已提交的查询。

### 生命周期

同时持续读取 stdout/stderr，避免管道阻塞；不能在内存中无限收集后再截断。原始输出流式保存到持久记录，默认只返回有界预览。

超时、取消或输出达到硬上限时，停止本机进程并等待清理；保留已得到的输出并标记不完整。终止本机 ADB 不等于证明设备端命令已经停止，未确认状态必须如实记录，不自动重放。

清理未完成时不接收同设备的新操作，返回清理失败。证据保存失败不能返回“记录可读取”。

## 7. 建议配置

以下数值是初始候选，不是已测得的最优值；真实验收后调整。网络与 USB 配置只保留一种目标形式。

```json
{
  "adb_path": "D:/platform-tools/adb.exe",
  "target": {"host": "<TV_IP>", "port": 5555},
  "auto_connect": true,
  "connect_timeout_seconds": 10,
  "command_timeout_seconds": 30,
  "cleanup_timeout_seconds": 5,
  "preview_max_chars_per_stream": 6000,
  "read_max_chars": 12000,
  "max_capture_bytes": 16777216,
  "output_directory": "./data/command-outputs"
}
```

`max_capture_bytes` 为 stdout/stderr 合计上限；超过后按输出不完整处理。连接加执行加清理的总预算应低于宿主工具调用超时，避免宿主先强制取消。UTF-8、时间基准、日志格式和目录权限在 README 中写清。

## 8. 分阶段实施与完成条件

| 阶段 | 工作 | 完成条件 |
|---|---|---|
| P0：协议与环境 | 确认首批 case/命令，创建项目，锁定依赖与公开接口，核对字符分页契约 | 示例配置能验证；接口文档无含糊字段；工具 Schema 可生成 |
| P1：执行闭环 | 固定目标、连接、命令校验、执行、错误和取消清理，短输出记录 | 脱敏模拟 ADB 覆盖正常、空值、非零退出、断连、超时、取消；SDK 能发现和调用工具 |
| P2：长输出与持久化 | 流式记录、预览、分页、大小限制、跨重启读取 | 分页可重建正常 UTF-8 完整输出；限制和异常不会被当完整成功；未重放原命令 |
| P3：真实设备验收 | 指定 USB 或网络目标执行首批查询，检查目标、输出和生命周期 | 有设备实际输出及记录；区分离线测试与真机结果；文档和接入片段完成 |
| P4：宿主接入 | 在 testing-react-agent 注册、证据入库、提示词/报告、任务工具选择和预算 | 原 clientidbase case 真实运行，读取、比对、终态与报告完整可追溯 |

新项目完成 P0–P3 即达到“MCP 服务交付”。P4 是另一项目的改动，未完成时不得宣称“原测试 case 已修好”。本计划不要求新项目直接修改当前测试 Agent。

## 9. 验证要求

离线测试必须模拟相关真实契约：ADB 退出码、stdout/stderr、状态探测、并发读取、取消和进程收尾。不以静态 import 或配置解析代替 MCP 初始化与工具发现。

重点检查：

1. 新属性名和新合法查询复用既有工具，不增加工具定义；工具列表最多两项。
2. argv 为空、非法参数、命令绕过在提交前拒绝；合法 Unicode、空格、引号等参数按已支持命令规则验证。
3. 精确设备目标保持一致；没有设备、offline、unauthorized 不落到其他设备。
4. 空输出保持为空，stderr 不污染 stdout；远端非零退出真实返回。Platform Tools 不支持可靠退出码时明确报不支持，不假装 exit_code=0。
5. 正常、长输出、单行超长、输出超限、磁盘错误、服务重启读取均有覆盖。
6. 超时/取消/清理失败不自动重放，已收集输出不被错误标记为完整。
7. MCP stdio 启动、initialize、list_tools、call_tool 的实际 SDK 闭环通过；诊断不污染协议 stdout。
8. 真实设备执行 getprop、settings get、dumpsys display；记录 ADB 版本、目标标识、实际耗时和输出完整性。需要完整逐字符比对时保存原始行尾，不默认 `.strip()` 丢掉信息。
9. 记录 tool Schema 大小和返回字符数，不编造跨模型通用的 token 节省比例。

## 10. 宿主接入交接

仅提供接入示例，路径均为占位值，不在当前仓库直接写入：

```json
{
  "mcpServers": {
    "adb": {
      "command": "<ADB_MCP_PROJECT>/.venv/Scripts/python.exe",
      "args": ["-m", "adb_mcp", "--config", "<ADB_MCP_PROJECT>/config.json"],
      "cwd": "<ADB_MCP_PROJECT>",
      "env": {"PYTHONUTF8": "1"}
    }
  }
}
```

这是需要合并进已有配置的片段，不是完整替换文件。保留 ATV、TV-UITree、StreamXpress 和用户设置。当前宿主命名规则下，工具名称预计为 `adb_run_adb_shell`、`adb_read_command_output`，接入时以实际发现列表为准。

宿主改造必须检查这些边界：

- `backend/app/tools.py`：已发现工具的选择，不要求 MCP 本身动态变更工具列表。
- `backend/app/execution/run_service.py` / `executor.py`：记录运行目录和每个任务实际暴露的工具集合；不能让审计快照与真实绑定范围不一致。
- `backend/app/execution/task_agent.py`：接受当前任务真实保存的命令证据；完整性和命令失败不能由模型文字覆盖。
- `backend/app/artifacts.py`、领域/持久化模型：命令记录与宿主 Artifact 建立明确转换。MCP output_id 不直接充当宿主 artifact_id；评估数据库迁移和已有截图数据。
- `backend/app/domain/execution/messages.py`、API 生成产物、前端和导出报告：命令证据的公开表示、查看和完整性标记一致。
- `backend/app/prompts/act.txt` / `judge.txt`：后台查询按文本事实说明，不要求虚构视觉观察；界面任务继续使用截图。
- 前一个任务的查询结果需可供下一个 Judge 读取，不能只在首任务的临时上下文中出现。

先按已确认计划所需能力选择工具，后台任务绑定 ADB 相关工具，混合任务绑定必要组合。宿主保持不理解具体 ADB 参数语义；命令规则属于新 MCP。工具不够时明确报告，不能静默猜测其他能力。

不直接把所有任务的 5 轮改成很大的常量。短查询通常需要执行一次、结束一次，长查询再加必要输出读取；规划时预留连接错误纠正和 finish_task 的预算。预算候选须经真机和模型运行确认。

原 case 验收：设备可达；属性值完整取得；TAM 的具体期望值作为真实输入；相同值 PASS、不同值 FAIL、必要输入缺失或输出不完整 BLOCKED；报告保留实际值、期望值、命令和证据来源。不会把命令执行成功等同于业务校验通过。

## 11. 开发时查阅的官方资料

- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)：使用公开 SDK 实现协议和类型化工具。当前主分支展示的是不同大版本接口；开发以实际锁定版本的源码、文档和 SDK 验证为准。
- [MCP Tools 规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)：工具列表、调用、结构化结果、错误和注解；注解不能当授权策略。
- [Android ADB 官方文档](https://developer.android.com/tools/adb)：目标选择、设备连接、Shell 参数引用和执行方式。

## 12. 可粘贴到新项目的启动要求

请在本项目开发独立的 Python ADB stdio MCP，按本计划完成 P0–P3。不要修改 testing-react-agent、ATV 或 TV-UITree。先核对本项目 AGENTS.md、Git 状态、Python 环境和锁定版本；使用公开 MCP SDK API。

初版提供 run_adb_shell 与 read_command_output 两个工具，支持固定 USB/网络设备上的受约束后台查询。不要为每个属性或 case 增加工具。统一返回实际目标、命令、stdout/stderr、退出码、时间、完整性和持久记录 ID；长输出流式保存、预览有界、按需读取。

完成命令参数校验、连接、超时、取消和进程清理，不自动重放不确定结果；真机验证前区分离线结果。历史输出采用统一的 Unicode 字符偏移分页，不另加一套行号分页。

交付源码、锁定依赖、示例配置、README、宿主接入说明和实际验证报告。测试范围包含 SDK stdio 闭环、正常与异常命令、长输出和生命周期。将宿主证据支持与任务工具筛选留作 P4 交接，不把新 MCP 完成等同于原 case 端到端修复。Git 提交使用带模块范围的 Conventional Commits，只暂存本项目任务文件；不默认推送或发布。
