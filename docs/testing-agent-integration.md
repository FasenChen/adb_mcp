# testing-react-agent 接入交接（P4）

本项目只交付独立 MCP。没有修改宿主 `mcp.json`、ATV、TV-UITree、StreamXpress、宿主数据库、提示词或前端。P4 尚未实施，原 clientidbase case 尚未端到端验收。

## 注册与工具名称

把 [mcp-host.example.json](mcp-host.example.json) 中 `adb` 条目合并进已有 `mcpServers`，替换全部项目路径占位符，保留其他服务和用户设置。使用新服务自己的 `.venv/Scripts/python.exe`、显式 `--config` 和 cwd，避免借用宿主 Python。

当前宿主适配器 0.3.2 的实际发现名称已验证为 `adb_run_adb_shell`、`adb_read_command_output`；MCP 自身名称不带 `adb_` 前缀。本项目没有自动注册。

## 简化后的输入输出

`adb_run_adb_shell` 只接受一个 query 枚举，例如：

```json
{"query":"clientidbase"}
```

一级命令类别由服务端固定映射决定。5 类、10 项白名单见 README；只读服务不开放 cmd / am。正常结果就是 stdout 原文，空值仍是成功结果；stderr、命令失败、截断及解码异常另有明确提示。工具结果没有 structuredContent 或输出 Schema，宿主不能再把文本当作 JSON 解析，也不能从 ToolMessage Artifact 获取命令元数据。

宿主应重启 MCP 并重新发现、绑定新工具。短输出直接使用第一次返回；长输出的提示会提供 `read_command_output` 所需 ID、流和下一位置，其返回也以原文为主。分页每次核对当前命令策略，旧宽权限记录保留在磁盘但不可通过工具读取。

## 本地记录与业务判断

完整元数据仍保存在 MCP 的 `output_directory/<output_id>/record.json`，内部格式为 `adb-command/v1`，两条原始流也继续保存。元数据和历史文件未删除、未迁移；普通短结果不向模型暴露 output_id。

P4 如需宿主 Artifact，仍需设计服务端记录的定位、原始流和元数据导入、run/task/step 关联。当前工具文本不承担这个归档接口，不把 MCP 记录 ID 当作宿主 artifact_id；本轮没有实现该链路。

判断边界：成功执行只代表取得事实，TAM 具体期望值仍需真实输入。同值 PASS、异值 FAIL；输入缺失、工具错误或证据不完整 BLOCKED。保留原始换行，明确用例要求的规范化方式；解码失败不能伪装成精确文本比对。

## 宿主修改路径与验收

- `backend/app/tools.py`：发现全部工具后，按确认计划为当前任务选择；后台任务绑定 ADB 两项，混合任务绑定所需组合。宿主不重复理解/校验具体 ADB 参数。
- `backend/app/execution/run_service.py`、`executor.py`：记录运行目录和每个任务实际绑定集合，审计快照与真实范围一致。
- `backend/app/execution/task_agent.py`：接受宿主实际保存的命令证据；完整性和命令失败不能被模型覆盖。
- `backend/app/artifacts.py`、领域及持久化模型：实现上述导入和关联，验证迁移及截图行为。
- `backend/app/domain/execution/messages.py`：新增公开命令证据表示，更新生成 API、前端、报告及导出消费者。
- `backend/app/prompts/act.txt`、`judge.txt`：后台查询接受文本事实，界面任务继续使用截图证据。

预算按查询、必要分页和 `finish_task` 规划，预留连接纠正时间，不把所有任务轮数整体放大。宿主工具调用超时应超过连接 + 查询 + 清理预算；默认候选 45 秒加落盘/协议余量，真实模型和混合任务验收后再确定。

最后使用同一目标核对 ATV / TV-UITree 配置，输入 TAM 具体期望值，真实运行原 case，检查读取、比对、终态、跨任务证据访问和报告。独立服务完成不替代这项验收。

## 适配器兼容性验证

使用宿主已有环境只读验证，不安装依赖、不更改宿主配置：

```powershell
# 在本项目目录执行；宿主环境路径应按实际安装核对。
../../.venv/Scripts/python.exe scripts/verify_host_adapter.py --config config.json --server-python .venv/Scripts/python.exe
```

脚本使用公开 `load_mcp_tools`、真实 stdio 调用及 `convert_to_openai_messages`，核对工具前缀、success/error 状态和模型消息中的文本数量。成功模型消息直接保留 stdout 原文，错误消息保留 SDK 错误说明；二者没有命令元数据 Artifact。脚本核对成功文本与本地记录逐字符相同，非法查询未创建记录。

此验证到公开消息转换边界为止，没有调用 LLM、没有启动测试 Agent 原 case，也没有证明现有 Judge 已接受命令证据。
