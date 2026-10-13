# ADB MCP 验证报告

日期：2026-10-13（Asia/Shanghai）。本报告区分离线、真实设备和宿主适配器边界，不把独立服务交付等同于原测试 case 完成。

## 范围和环境

- P0–P2：源码、独立环境、命令策略、生命周期、持久化、分页及 SDK stdio 闭环已实现。
- P3：使用用户指定的 `192.168.1.109`，按计划默认端口选定 `192.168.1.109:5555`，真实只读验收通过。
- P4：未实施；未修改宿主配置、后端、前端、ATV 或 TV-UITree。
- Python 3.12.10；MCP SDK 1.30.0；Pydantic 2.14.1；新项目独立 `.venv`，依赖精确版本及哈希保存在 `requirements.lock`。
- ADB 1.0.41 / Platform Tools **36.0.0-13206524**，路径 `D:/platform-tools/adb.exe`；目标列表精确匹配 `device`，目标 features 包含 `shell_v2`。

开发前本目录只有计划，没有现有实现；父仓库已有修改。计划中“文件保存在工作区外”是历史描述，当前实际位于宿主的 `mcp_servers/adb_mcp`。本次只在此目录创建独立包和文档，没有创建嵌套 Git 仓库或改动父项目功能。

## 离线和协议检查

执行以下必要检查：

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/ruff.exe check src tests scripts
.venv/Scripts/ruff.exe format --check src tests scripts
```

初版结果：**79 项测试通过**，Ruff 静态检查与格式检查通过。带 `--require-hashes` 的锁定依赖同步、独立包安装及 `adb-query-mcp --check-config` 命令入口也实际通过。取消记录 ID 的 stderr 诊断补充后，相关取消与协议用例再次定向通过。

覆盖范围：

| 边界 | 实际验证 |
|---|---|
| 命令策略 | 新属性复用相同工具；空 argv、控制字符、shell 串联/替换、写 settings、未知 dumpsys 服务及附加参数拒绝 |
| 引用 | 模拟器用 POSIX 解析核对 Unicode、空格和单双引号；真机另有只读引用烟测 |
| 固定设备 | 精确 serial、其他设备存在、offline、unauthorized、重复目标、网络 connect 一次及关闭 auto_connect |
| 退出状态 | 正常、空值、远端非零、准备后断连；stdout/stderr 分离，不根据业务值判断 PASS/FAIL |
| 进程生命周期 | 查询超时、连接总预算、直接取消、AnyIO 取消、创建进程期间取消、清理失败及后续操作阻断 |
| 存储 | 大流并发、单行长输出、合计硬上限和刚好上限、写盘/提交/目录错误、未提交及损坏记录拒读 |
| Unicode | 中文、emoji、组合码点、原始 CR/LF、无效 UTF-8、合法替换字符；分页重建文本及原始字节比对 |
| MCP | stdio initialize/list_tools/call_tool，2 个工具、输入及输出 Schema、结构化输出、isError、真实协议取消及下一次操作恢复 |
| 重启 | 独立存储实例和重启的 stdio 服务读取历史，不重新执行命令 |

协议取消验收先确认模拟器已进入查询，再发送标准 `notifications/cancelled`；收到取消错误，已保存不完整记录，后续查询可继续。进程测试使用真实 Python 子进程及管道；仅模拟 ADB 的设备行为，不把模拟结果描述成真实硬件错误注入。

## 真实设备（初版验收记录）

通过项目自己的 SDK stdio 客户端运行：

```powershell
.venv/Scripts/python.exe scripts/verify_device.py --config config.json --report data/device-validation.json
```

初版采样时间为北京时间 11:30，数据如下；本次审计修复没有重复真机查询。耗时从准备到查询及进程收尾，以单调时钟计数，不能作为跨模型或跨设备性能结论。

| 查询 | stdout 原始字节 | stderr 原始字节 | 耗时 ms | 返回 JSON 字符 | 状态 |
|---|---:|---:|---:|---:|---|
| getprop ro.com.google.clientidbase | 21 | 0 | 188 | 515 | exit 0，完整，无解码错误 |
| settings get global device_name | 9 | 0 | 187 | 503 | exit 0，完整，无解码错误 |
| dumpsys display | 35091 | 0 | 203 | 6621 | exit 0，完整；stdout 仅预览前 6000 字符 |
| settings get secure 含 Unicode/空格/单双引号的不存在键 | 6 | 0 | 187 | 506 | exit 0，返回 null，引用烟测通过 |

两项工具的紧凑列表 JSON（含描述、输入/输出 Schema 及注解）共 **4431 UTF-8 字节**。返回字符数包含 JSON 元数据与转义；没有估算通用 token 节省比例。

关闭并重启服务后，用 `read_command_output` 分页读取上述 stdout/stderr；重建文本与各自保存的原始 UTF-8 字节逐字符一致，保留 CR/LF，没有再次执行原查询。验收脚本保存每条流的 SHA-256、实际目标、argv、记录 ID、耗时和完整性。固定的设备端只读 `exit 7` 夹具真实返回 7，证明该目标的远端退出状态通道有效；它不开放为工具。

Unicode 不存在键的烟测证明命令引用未导致 Shell 语法错误，不证明该键存在，也不替代离线参数解析比对。真实验收没有制造断网、unauthorized、磁盘满或设备端长时间挂起；这些异常的结论来自离线测试。终止本机 ADB 后设备端状态仍按未确认处理。

完整设备结果位于本地 `data/device-validation.json` 和 `data/command-outputs/<output_id>/`。真实配置、原始输出及运行报告均被 Git 忽略；本文只记录必要指标。

## 宿主适配器兼容性（初版验收记录）

使用宿主既有 Python 环境，只读取其公开适配器 API，启动项目自己的服务：

```powershell
../../.venv/Scripts/python.exe scripts/verify_host_adapter.py --config config.json --server-python .venv/Scripts/python.exe
```

实测 MCP 1.30.0、langchain-mcp-adapters 0.3.2、langchain-core 1.6.9：

- 发现名称为 `adb_run_adb_shell` / `adb_read_command_output`。
- 成功调用和策略错误均转换成只有 1 个 JSON 文本块的 ToolMessage；模型兼容消息没有重复附加结构化数据。
- 成功的结构化数据保留在 Artifact，模型消息不包含 Artifact；错误状态为 `error`。
- 未调用 LLM，未注册进宿主 `mcp.json`，未运行原 case；验证只覆盖公开适配器与消息转换边界。

本地记录为 `data/host-adapter-validation.json`。P4 仍须实际导入命令证据、修改任务提示词/报告、选择工具，并提供 TAM 具体期望值后验收原 case。

## 前次 Ponytail 审计整改（枚举变更前）

本轮修复 2 项错误并删除 1 项重复校验，未新增配置、依赖或协议字段：

- 非法超长命令不再回显原始 argv，返回空 argv 与简短 `invalid_command` 原因。固定验证目标下，10 万字符参数的错误 JSON 从 **100470 字符降为 458 字符**；SDK stdio 回归测试同时确认不启动模拟 ADB、不创建记录，结构化数据与唯一文本块一致。正常和非零退出请求仍保留原始 argv。
- `./adb.exe`、`.\adb.exe` 及子目录路径按配置文件目录解析，配置与工作目录不同的用例通过；裸 `adb` / `adb.exe` 保留 PATH 查找，绝对路径保留选定程序。
- 删除存储层第二次手写 ID 正则和未再使用的导入，保留 `OutputRequest` 校验、读取上限、记录匹配和完整性检查。既有非法 ID、路径穿越、分页和重启读取测试通过。

新回归测试先在修复前复现两种路径错误和超长回显；修复后完整结果为 **86 passed，20.39 秒**（初版 79 项加 7 项）。Ruff 静态检查与格式检查通过，18 个 Python 文件格式一致；已安装的 `adb-query-mcp --check-config` 入口实际通过。

修复前后两项工具的描述、输入/输出 Schema 及注解完全一致；排序后紧凑列表 JSON 的 SHA-256 均为 `b2d8cdf02d5b5b94fb5b237185700fd8a7bb1331823918efc094e2d7f7ccd931`。`schema_version` 保持 `adb-command/v1`。Ponytail 按单用户、单固定设备复核本次整改，三项发现均已处理，没有遗留本次审查的阻断问题。

本轮只执行离线、SDK 协议及配置入口验证，没有重新调用电视、宿主适配器或 LLM，因为修改未涉及设备执行流程；上述真机和适配器指标属于初版验收事实。P4 仍未实施。

## 命令枚举与权限收敛（当前实现）

本次按用户确认需求，将 `run_adb_shell` 的输入从 `argv` 改为 `command + query` 两个枚举；工具数量仍为 2，返回 `argv` 和 `adb-command/v1` 不变。5 个只读类别、10 项固定映射见 README。类别与查询必须匹配；执行和每次历史读取复用同一名单，旧宽权限记录不可通过工具读取但文件保留。任意属性/设置键、`settings list`、`dumpsys display` 已移出权限范围；`cmd` / `am` 没有开放。

离线结果为 **153 passed，29.98 秒**；Ruff 静态检查和 18 个 Python 文件的格式检查通过。覆盖全部 10 个允许映射、40 个错误类别/查询组合、旧 argv 请求、非法枚举/类型、10 万字符非法选项、执行前拒绝、两条历史流策略检查和 SDK 重启后拒读。模拟器通过独立测试场景控制流、退出码与延时，不在生产名单中增加测试属性；既有取消、清理、硬上限及 Unicode 分页断言保留。CLI 配置检查实际通过。

真机采样时间为 **2026-10-13 17:06（Asia/Shanghai）**。使用已有配置和精确目标 `192.168.1.109:5555`，通过 SDK stdio 实际执行全部 10 项只读查询：均退出 0、完整采集，无解码错误；stdout 分别为 21、14、14、16、15、2、3234、18、1534、2854 字节，stderr 均为 0。重启后分页重建两条原始流并逐字符比对通过。没有执行恢复出厂或打开界面，也没有判定设备认证是否通过。

初次运行在完成 10 项查询和重启分页后，额外 `exit 7` 探针因 ADB 目标已不在列表而失败。将探针移到已准备的 stdio 会话内，不改变退出码及双流断言；再次完整验收通过，实际返回 7。网络目标消失的具体外部原因未确认。该只读夹具不开放为 MCP 查询。

当前两项工具列表 JSON（含输入/输出 Schema、描述和注解）为 **4613 UTF-8 字节**；初版为 4431 字节。此版重点是权限收敛与调用选择，未声称减少模型 token 或项目上下文。真实报告和证据位于被忽略的 `data/device-enum-validation.json` 与 `data/command-outputs/`，不提交设备标识值或 MAC 信息。

宿主已有适配器 0.3.2 / langchain-core 1.6.9 / MCP 1.30.0 实际通过新枚举输入的成功调用与错误组合调用，仍各只有 1 个模型消息 JSON 文本块，成功结构化 Artifact 不重复送入模型。结果位于被忽略的 `data/host-adapter-enum-validation.json`。未调用 LLM、未更改宿主配置，P4 仍未实施；宿主需要重启服务并重新发现、绑定更新后的 Schema。

按 Ponytail 的单用户、单固定设备范围复核，未发现本次变更的阻断问题；没有新增依赖、配置开关、兼容入口或通用 Shell 执行层。

## Git 与交付边界

开发前抽查的 3 个旧提交均使用带模块范围的 Conventional Commits，无需改写历史。开发计划作为单独检查点提交，实现和本轮修复另行提交；暂存范围仅本目录相关文件。父项目原有修改及新增服务目录保留；真实配置和运行记录未暂存，未执行宿主数据库迁移。

用户已授权将独立项目发布至 `https://github.com/FasenChen/adb_mcp.git` 的 `main`。发布内容从宿主仓库的 `mcp_servers/adb_mcp` 目录历史拆分，保留项目自己的提交，不更改宿主原有 origin，不上传其他服务或宿主代码。实际远程 SHA 和推送结果以交付说明及远程分支为准，不在验收报告中预写发布成功。
