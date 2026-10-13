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

结果：**79 项测试通过**，Ruff 静态检查与格式检查通过。带 `--require-hashes` 的锁定依赖同步、独立包安装及 `adb-query-mcp --check-config` 命令入口也实际通过。取消记录 ID 的 stderr 诊断补充后，相关取消与协议用例再次定向通过。

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

## 真实设备

通过项目自己的 SDK stdio 客户端运行：

```powershell
.venv/Scripts/python.exe scripts/verify_device.py --config config.json --report data/device-validation.json
```

本次采样时间为北京时间 11:30，数据如下。耗时从准备到查询及进程收尾，以单调时钟计数，不能作为跨模型或跨设备性能结论。

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

## 宿主适配器兼容性

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

## Git 与交付边界

开发前抽查的 3 个旧提交均使用带模块范围的 Conventional Commits，无需改写历史。开发计划作为单独检查点提交，实现另行提交；暂存范围仅本目录源码、锁定依赖、示例配置、测试和文档。父项目原有修改及新增服务目录保留；真实配置和运行记录未暂存。没有执行推送、发布或宿主数据库迁移。
