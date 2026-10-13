"""Read-only verification using the host's existing adapter environment (no LLM call)."""

import argparse
import asyncio
import json
import os
from importlib.metadata import version
from pathlib import Path

from langchain_core.messages import convert_to_openai_messages
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def verify(config_path: Path, server_python: Path, report_path: Path) -> None:
    project = Path(__file__).parents[1]
    params = StdioServerParameters(
        command=str(server_python.resolve()),
        args=["-m", "adb_mcp", "--config", str(config_path.resolve())],
        cwd=str(project),
        env=dict(os.environ) | {"PYTHONUTF8": "1"},
    )
    observations = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await load_mcp_tools(session, server_name="adb", tool_name_prefix=True)
            assert {tool.name for tool in tools} == {
                "adb_run_adb_shell",
                "adb_read_command_output",
            }
            tool = next(tool for tool in tools if tool.name == "adb_run_adb_shell")
            for index, arguments in enumerate(
                [
                    {"command": "getprop", "query": "clientidbase"},
                    {"command": "ip", "query": "clientidbase"},
                ]
            ):
                message = await tool.ainvoke(
                    {
                        "type": "tool_call",
                        "id": f"adapter-check-{index}",
                        "name": tool.name,
                        "args": arguments,
                    }
                )
                outgoing = convert_to_openai_messages([message], text_format="block")[0]
                content = outgoing["content"]
                assert isinstance(content, list) and len(content) == 1
                result = json.loads(content[0]["text"])
                assert message.status == ("success" if index == 0 else "error")
                assert result["error_code"] == (None if index == 0 else "invalid_command")
                assert "artifact" not in outgoing
                observations.append(
                    {
                        "status": message.status,
                        "text_blocks": len(content),
                        "return_text_chars": len(content[0]["text"]),
                        "structured_artifact_present": message.artifact is not None,
                        "artifact_sent_in_model_message": False,
                        "output_id": result["output_id"],
                    }
                )
    report = {
        "mcp": version("mcp"),
        "langchain-mcp-adapters": version("langchain-mcp-adapters"),
        "langchain-core": version("langchain-core"),
        "observations": observations,
        "live_llm_called": False,
        "host_configuration_modified": False,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--server-python", required=True, type=Path)
    parser.add_argument("--report", default=Path("data/host-adapter-validation.json"), type=Path)
    args = parser.parse_args()
    asyncio.run(verify(args.config, args.server_python, args.report))


if __name__ == "__main__":
    main()
