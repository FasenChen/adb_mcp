"""Opt-in acceptance of configured commands via the MCP SDK; review side effects first."""

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from adb_mcp.commands import CommandResult
from adb_mcp.config import load_config


async def capture_process(*args: str) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(10):
            stdout, stderr = await process.communicate()
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    return process.returncode, stdout, stderr


async def verify(path: Path, report_path: Path) -> None:
    config = load_config(path)
    code, version, _ = await capture_process(config.adb_path, "version")
    assert code == 0
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "adb_mcp", "--config", str(path.resolve())],
        cwd=str(Path(__file__).parents[1]),
        env=dict(os.environ) | {"PYTHONUTF8": "1"},
    )
    report = {
        "verified_at": datetime.now(UTC).isoformat(),
        "target_serial": config.target_serial,
        "adb_version": version.decode("utf-8", "replace"),
        "queries": [],
    }
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert {tool.name for tool in tools} == {"run_adb_shell", "read_command_output"}
            assert all(tool.outputSchema is None for tool in tools)
            report["tool_schemas_utf8_bytes"] = len(
                json.dumps(
                    [tool.model_dump(mode="json", exclude_none=True) for tool in tools],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            for command in config.allowed_commands:
                argv = shlex.split(command)[2:]
                before = set(config.output_directory.glob("*/record.json"))
                response = await session.call_tool("run_adb_shell", {"command": command})
                assert response.structuredContent is None
                records = set(config.output_directory.glob("*/record.json")) - before
                assert len(records) == 1
                result = CommandResult.model_validate_json(records.pop().read_bytes())
                assert response.content[0].text == result.stdout
                assert not response.isError and result.exit_code == 0, result.error_code
                assert result.target_serial == config.target_serial
                assert result.argv == argv
                assert result.capture_complete and not result.decoding_errors
                query = result.model_dump(exclude={"stdout", "stderr"})
                assert result.command == command
                query["return_text_chars"] = sum(len(block.text) for block in response.content)
                for stream in ("stdout", "stderr"):
                    raw = (
                        config.output_directory / result.output_id / f"{stream}.bin"
                    ).read_bytes()
                    query[f"{stream}_sha256"] = hashlib.sha256(raw).hexdigest()
                    decoded = raw.decode("utf-8")
                    assert decoded[: config.preview_max_chars_per_stream] == getattr(result, stream)
                report["queries"].append(query)

            # A fixed read-only shell fixture verifies the actual remote exit-code channel.
            # Probe while the prepared device session is still active; never expose it as a tool.
            code, stdout, stderr = await capture_process(
                config.adb_path,
                "-s",
                config.target_serial,
                "shell",
                "-T",
                "-n",
                "exit 7",
            )
            assert code == 7 and stdout == b"" and stderr == b""
            report["remote_exit_code_probe"] = code

    # Restart the server and reconstruct streams only from stored evidence.
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for query in report["queries"]:
                for stream in ("stdout", "stderr"):
                    raw = (
                        config.output_directory / query["output_id"] / f"{stream}.bin"
                    ).read_bytes()
                    expected = raw.decode("utf-8")
                    parts = []
                    start = 0
                    while True:
                        response = await session.call_tool(
                            "read_command_output",
                            {
                                "output_id": query["output_id"],
                                "stream": stream,
                                "start_char": start,
                                "max_chars": config.read_max_chars,
                            },
                        )
                        assert not response.isError
                        assert response.structuredContent is None
                        text = response.content[0].text
                        assert text == expected[start : start + config.read_max_chars]
                        parts.append(text)
                        start += len(text)
                        if start == len(expected):
                            break
                        assert text
                    assert "".join(parts) == expected
    report["restart_pagination_verified"] = True
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("data/device-validation.json"))
    args = parser.parse_args()
    asyncio.run(verify(args.config, args.report))


if __name__ == "__main__":
    main()
