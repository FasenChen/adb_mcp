import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification, JSONRPCRequest

from adb_mcp.commands import COMMANDS
from adb_mcp.config import Config
from adb_mcp.server import create_server

FIXTURE = Path(__file__).with_name("mcp_fixture.py")


def parameters(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "adb_path": "fake-adb",
                "target": {"serial": "test-device"},
                "output_directory": "outputs",
                "preview_max_chars_per_stream": 20,
                "read_max_chars": 100,
            }
        )
    )
    return StdioServerParameters(
        command=sys.executable,
        args=[str(FIXTURE), "--config", str(config_path)],
        env=dict(os.environ) | {"PYTHONUTF8": "1"},
    )


def verify_one_text_and_structure(result):
    assert len(result.content) == 1 and result.content[0].type == "text"
    assert json.loads(result.content[0].text) == result.structuredContent


async def test_stdio_all_fixed_queries(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_LOG", str(tmp_path / "adb.log"))
    async with stdio_client(parameters(tmp_path)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for query, argv in COMMANDS.items():
                result = await session.call_tool(
                    "run_adb_shell", {"command": argv[0], "query": query}
                )
                assert not result.isError
                assert result.structuredContent["argv"] == argv
                verify_one_text_and_structure(result)
    import shlex

    from conftest import calls

    assert [shlex.split(args[-1]) for args in calls(tmp_path) if "shell" in args] == list(
        COMMANDS.values()
    )


async def test_stdio_initialize_list_call_and_error(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_QUERY_MODES", json.dumps({"ro.oem.key1": "fail"}))
    async with stdio_client(parameters(tmp_path)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            assert {tool.name for tool in listed.tools} == {
                "run_adb_shell",
                "read_command_output",
            }
            assert all(tool.outputSchema and tool.annotations.readOnlyHint for tool in listed.tools)
            normal = await session.call_tool(
                "run_adb_shell", {"command": "getprop", "query": "boot_serialno"}
            )
            assert not normal.isError
            verify_one_text_and_structure(normal)
            assert normal.structuredContent["stdout"] == "ro.boot.serialno\r\n"
            assert normal.structuredContent["argv"] == ["getprop", "ro.boot.serialno"]
            failed = await session.call_tool(
                "run_adb_shell", {"command": "getprop", "query": "oem_key1"}
            )
            assert failed.isError and failed.structuredContent["exit_code"] == 7
            assert failed.structuredContent["argv"] == ["getprop", "ro.oem.key1"]
            verify_one_text_and_structure(failed)
            invalid = await session.call_tool(
                "run_adb_shell", {"command": "ip", "query": "boot_serialno"}
            )
            assert invalid.isError and invalid.structuredContent["error_code"] == "invalid_command"
            assert invalid.structuredContent["argv"] == []
            for arguments in [
                {"argv": ["getprop", "ro.boot.serialno"]},
                {"command": "cmd", "query": "test_harness_state"},
                {"command": "am", "query": "features"},
                {"command": "getprop", "query": "arbitrary_shell"},
                {"command": 1, "query": "boot_serialno"},
                {"command": "getprop", "query": ["boot_serialno"]},
                {},
            ]:
                assert (await session.call_tool("run_adb_shell", arguments)).isError
            page = await session.call_tool(
                "read_command_output",
                {
                    "output_id": normal.structuredContent["output_id"],
                    "max_chars": 5,
                },
            )
            assert not page.isError and page.structuredContent["text"] == "ro.bo"
            verify_one_text_and_structure(page)
            for options in [{"start_char": -1}, {"max_chars": 101}, {"start_char": 100}]:
                result = await session.call_tool(
                    "read_command_output",
                    {
                        "output_id": normal.structuredContent["output_id"],
                    }
                    | options,
                )
                assert result.isError


async def test_stdio_oversized_enum_error_is_bounded_without_adb(tmp_path, monkeypatch):
    log = tmp_path / "adb.log"
    monkeypatch.setenv("FAKE_ADB_LOG", str(log))
    async with stdio_client(parameters(tmp_path)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.call_tool(
                "run_adb_shell",
                {
                    "command": "getprop",
                    "query": "x" * 100000,
                },
            )
            assert response.isError
            assert len(response.content[0].text) < 2048
    assert not log.exists()
    assert not (tmp_path / "outputs").exists()


async def test_stdio_saved_output_survives_server_restart(tmp_path):
    params = parameters(tmp_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "run_adb_shell",
                {
                    "command": "getprop",
                    "query": "boot_serialno",
                },
            )
            output_id = result.structuredContent["output_id"]
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page = await session.call_tool("read_command_output", {"output_id": output_id})
            assert page.structuredContent["text"] == "ro.boot.serialno\r\n"


async def test_schema_exposes_category_and_query_enums(tmp_path):
    config = Config(target={"serial": "test-device"}, output_directory=tmp_path)
    schemas = [tool.model_dump(mode="json") for tool in await create_server(config).list_tools()]
    assert len(schemas) == 2
    text = json.dumps(schemas)
    assert "ro.com.google.clientidbase" not in text
    assert "vendor.new_property" not in text
    schema = next(tool["inputSchema"] for tool in schemas if tool["name"] == "run_adb_shell")
    assert schema["required"] == ["command", "query"]
    assert set(schema["properties"]) == {"command", "query"}
    assert schema["properties"]["command"]["enum"] == [
        "getprop",
        "pm",
        "settings",
        "ifconfig",
        "ip",
    ]
    assert len(schema["properties"]["query"]["enum"]) == 10


@pytest.mark.parametrize("port", [True, "5555"])
async def test_cli_config_errors_only_on_stderr(tmp_path, port):
    import asyncio

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"target": {"host": "192.0.2.1", "port": port}}))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "adb_mcp",
        "--config",
        str(path),
        "--check-config",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 2 and stdout == b"" and b"Configuration invalid" in stderr


async def test_stdio_protocol_cancel_commits_partial_and_accepts_next_query(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_LOG", str(tmp_path / "adb.log"))
    monkeypatch.setenv(
        "FAKE_ADB_QUERY_MODES",
        json.dumps({"ro.boot.serialno": "slow", "ro.serialno": "empty"}),
    )
    params = parameters(tmp_path)

    async with stdio_client(params) as (read, write):

        async def send(message):
            await write.send(SessionMessage(JSONRPCMessage(message)))

        async def response(request_id):
            async with asyncio.timeout(5):
                while True:
                    item = await read.receive()
                    assert not isinstance(item, Exception)
                    if getattr(item.message.root, "id", None) == request_id:
                        return item.message.root

        await send(
            JSONRPCRequest(
                jsonrpc="2.0",
                id="init",
                method="initialize",
                params={
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "cancel-acceptance", "version": "1"},
                },
            )
        )
        assert (await response("init")).result["serverInfo"]["name"] == "adb-query"
        await send(JSONRPCNotification(jsonrpc="2.0", method="notifications/initialized"))
        await send(
            JSONRPCRequest(
                jsonrpc="2.0",
                id="slow",
                method="tools/call",
                params={
                    "name": "run_adb_shell",
                    "arguments": {"command": "getprop", "query": "boot_serialno"},
                },
            )
        )
        from test_adb import wait_for_query

        await wait_for_query(tmp_path)
        await send(
            JSONRPCNotification(
                jsonrpc="2.0",
                method="notifications/cancelled",
                params={
                    "requestId": "slow",
                    "reason": "acceptance cancellation",
                },
            )
        )
        assert (await response("slow")).error.message == "Request cancelled"
        await send(
            JSONRPCRequest(
                jsonrpc="2.0",
                id="next",
                method="tools/call",
                params={
                    "name": "run_adb_shell",
                    "arguments": {"command": "getprop", "query": "serialno"},
                },
            )
        )
        assert not (await response("next")).result["isError"]
    records = [
        json.loads(path.read_text()) for path in (tmp_path / "outputs").glob("*/record.json")
    ]
    cancelled = next(record for record in records if record["argv"][-1] == "ro.boot.serialno")
    assert cancelled["error_code"] == "cancelled" and not cancelled["capture_complete"]
    assert cancelled["stdout"] == "before timeout\r\n"


async def test_stdio_restart_rejects_output_outside_current_policy(tmp_path):
    params = parameters(tmp_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "run_adb_shell", {"command": "getprop", "query": "boot_serialno"}
            )
            output_id = result.structuredContent["output_id"]
    record_path = tmp_path / "outputs" / output_id / "record.json"
    record = json.loads(record_path.read_text())
    record["argv"] = ["dumpsys", "display"]
    record_path.write_text(json.dumps(record))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page = await session.call_tool("read_command_output", {"output_id": output_id})
            assert page.isError and "current read-only query list" in page.content[0].text
    assert record_path.exists()
