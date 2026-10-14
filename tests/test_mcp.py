import asyncio
import json
import os
import shlex
import sys
from pathlib import Path

import pytest
from conftest import ALLOWED_COMMANDS
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification, JSONRPCRequest

from adb_mcp.config import Config
from adb_mcp.server import create_server

FIXTURE = Path(__file__).with_name("mcp_fixture.py")


def parameters(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "adb_path": "fake-adb",
                "allowed_commands": ALLOWED_COMMANDS,
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


def verify_text_result(result):
    assert result.structuredContent is None
    assert result.content and all(block.type == "text" for block in result.content)
    return result.content[0].text


def latest_record(tmp_path):
    path = max(
        (tmp_path / "outputs").glob("*/record.json"), key=lambda path: path.stat().st_mtime_ns
    )
    return json.loads(path.read_text(encoding="utf-8"))


async def test_stdio_all_fixed_queries(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_LOG", str(tmp_path / "adb.log"))
    async with stdio_client(parameters(tmp_path)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for command in ALLOWED_COMMANDS:
                argv = shlex.split(command)[2:]
                result = await session.call_tool("run_adb_shell", {"command": command})
                assert not result.isError
                assert latest_record(tmp_path)["argv"] == argv
                assert verify_text_result(result) == latest_record(tmp_path)["stdout"]
    from conftest import calls

    assert [shlex.split(args[-1]) for args in calls(tmp_path) if "shell" in args] == list(
        shlex.split(command)[2:] for command in ALLOWED_COMMANDS
    )


async def test_stdio_uses_configured_exact_allowlist(tmp_path, monkeypatch):
    from conftest import calls

    monkeypatch.setenv("FAKE_ADB_LOG", str(tmp_path / "adb.log"))
    params = parameters(tmp_path)
    path = tmp_path / "config.json"
    config = json.loads(path.read_text())
    command = "adb shell getprop 'vendor.new property'"
    config["allowed_commands"] = [command]
    path.write_text(json.dumps(config))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for rejected in [
                "adb shell getprop vendor.new property",
                command + " ",
                command + "\n",
                command + "; reboot",
                "adb shell ifconfig",
            ]:
                response = await session.call_tool("run_adb_shell", {"command": rejected})
                assert response.isError and calls(tmp_path) == []
                assert not (tmp_path / "outputs").exists()
            response = await session.call_tool("run_adb_shell", {"command": command})
            assert not response.isError
            record = latest_record(tmp_path)
            assert record["command"] == command
            assert record["argv"] == ["getprop", "vendor.new property"]
            assert shlex.split(calls(tmp_path)[-1][-1]) == record["argv"]


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
            assert all(tool.outputSchema is None for tool in listed.tools)
            normal = await session.call_tool(
                "run_adb_shell", {"command": "adb shell getprop ro.boot.serialno"}
            )
            assert not normal.isError
            normal_record = latest_record(tmp_path)
            assert verify_text_result(normal) == "ro.boot.serialno\r\n"
            assert len(normal.content) == 1
            assert normal_record["stdout"] == "ro.boot.serialno\r\n"
            assert normal_record["argv"] == ["getprop", "ro.boot.serialno"]
            failed = await session.call_tool(
                "run_adb_shell", {"command": "adb shell getprop ro.oem.key1"}
            )
            failed_record = latest_record(tmp_path)
            assert failed.isError and failed_record["exit_code"] == 7
            assert failed_record["argv"] == ["getprop", "ro.oem.key1"]
            verify_text_result(failed)
            invalid = await session.call_tool("run_adb_shell", {"command": "unsupported"})
            assert invalid.isError and invalid.structuredContent is None
            for arguments in [
                {"argv": ["getprop", "ro.boot.serialno"]},
                {"query": "boot_serialno"},
                {"command": "testharness_enable"},
                {"command": "open_settings"},
                {"command": "arbitrary_shell"},
                {"command": 1},
                {"command": ["boot_serialno"]},
                {},
            ]:
                assert (await session.call_tool("run_adb_shell", arguments)).isError
            page = await session.call_tool(
                "read_command_output",
                {
                    "output_id": normal_record["output_id"],
                    "max_chars": 5,
                },
            )
            assert not page.isError and verify_text_result(page) == "ro.bo"
            cursor = json.loads(page.content[-1].text.split("read_command_output ")[1])
            assert cursor == {
                "output_id": normal_record["output_id"],
                "stream": "stdout",
                "start_char": 5,
            }
            for options in [{"start_char": -1}, {"max_chars": 101}, {"start_char": 100}]:
                result = await session.call_tool(
                    "read_command_output",
                    {
                        "output_id": normal_record["output_id"],
                    }
                    | options,
                )
                assert result.isError


async def test_stdio_oversized_command_error_is_bounded_without_adb(tmp_path, monkeypatch):
    log = tmp_path / "adb.log"
    monkeypatch.setenv("FAKE_ADB_LOG", str(log))
    async with stdio_client(parameters(tmp_path)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.call_tool(
                "run_adb_shell",
                {
                    "command": "x" * 100000,
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
                    "command": "adb shell getprop ro.boot.serialno",
                },
            )
            assert not result.isError and verify_text_result(result) == "ro.boot.serialno\r\n"
            output_id = latest_record(tmp_path)["output_id"]
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page = await session.call_tool("read_command_output", {"output_id": output_id})
            assert verify_text_result(page) == "ro.boot.serialno\r\n"
            assert len(page.content) == 1


@pytest.mark.parametrize(
    ("mode", "is_error", "notice"),
    [
        ("empty", False, ""),
        ("fail", True, "error=command_failed; exit_code=7"),
        ("warning", False, "[stderr]\nquery warning\n"),
        ("invalid_utf8", False, "decoding_errors=true"),
        ("limit", True, "capture_complete=false"),
        ("long", False, "stderr_truncated=true"),
    ],
)
async def test_stdio_raw_output_and_only_required_notices(
    tmp_path, monkeypatch, mode, is_error, notice
):
    monkeypatch.setenv("FAKE_ADB_QUERY_MODE", mode)
    params = parameters(tmp_path)
    if mode == "limit":
        config_path = tmp_path / "config.json"
        config = json.loads(config_path.read_text())
        config["max_capture_bytes"] = 128
        config_path.write_text(json.dumps(config))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "run_adb_shell", {"command": "adb shell getprop ro.boot.serialno"}
            )
            record = latest_record(tmp_path)
            assert result.isError is is_error
            assert verify_text_result(result) == record["stdout"]
            if notice:
                assert notice in "\n".join(block.text for block in result.content)
            else:
                assert len(result.content) == 1 and result.content[0].text == ""
            for field in ("target_serial", "started_at", "stdout_bytes", "schema_version"):
                assert field in record
                assert all(field not in block.text for block in result.content)
            if mode in {"long", "limit"}:
                cursors = [
                    json.loads(line.removeprefix("read_command_output "))
                    for block in result.content
                    for line in block.text.splitlines()
                    if line.startswith("read_command_output ")
                ]
                assert {cursor["stream"] for cursor in cursors} == (
                    {"stdout", "stderr"} if mode == "long" else {"stdout"}
                )
                for cursor in cursors:
                    assert cursor["output_id"] == record["output_id"]
                    assert cursor["start_char"] == len(record[cursor["stream"]])
                    page = await session.call_tool("read_command_output", cursor | {"max_chars": 7})
                    raw = (
                        (tmp_path / "outputs" / record["output_id"] / f"{cursor['stream']}.bin")
                        .read_bytes()
                        .decode("utf-8")
                    )
                    assert (
                        verify_text_result(page)
                        == raw[cursor["start_char"] : cursor["start_char"] + 7]
                    )
                    next_cursor = json.loads(
                        page.content[-1].text.split("read_command_output ")[1].splitlines()[0]
                    )
                    assert next_cursor["start_char"] == cursor["start_char"] + len(
                        page.content[0].text
                    )
            if mode == "fail":
                page = await session.call_tool(
                    "read_command_output", {"output_id": record["output_id"], "stream": "stderr"}
                )
                assert not page.isError and verify_text_result(page) == "remote error\n"
                assert "source_error=command_failed" in page.content[-1].text
            if mode == "invalid_utf8":
                page = await session.call_tool(
                    "read_command_output", {"output_id": record["output_id"]}
                )
                assert not page.isError and verify_text_result(page) == record["stdout"]
                assert "decoding_errors=true" in page.content[-1].text


async def test_schema_only_exposes_command_and_no_output_metadata(tmp_path):
    config = Config(target={"serial": "test-device"}, output_directory=tmp_path)
    schemas = [tool.model_dump(mode="json") for tool in await create_server(config).list_tools()]
    assert len(schemas) == 2
    text = json.dumps(schemas)
    assert "ro.com.google.clientidbase" not in text
    schema = next(tool["inputSchema"] for tool in schemas if tool["name"] == "run_adb_shell")
    assert schema["required"] == ["command"]
    assert set(schema["properties"]) == {"command"}
    assert schema["properties"]["command"]["type"] == "string"
    assert all(tool["outputSchema"] is None for tool in schemas)
    assert "stdout_bytes" not in text and "started_at" not in text
    run_tool = next(tool for tool in schemas if tool["name"] == "run_adb_shell")
    assert run_tool["annotations"]["readOnlyHint"] is None


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
                    "arguments": {"command": "adb shell getprop ro.boot.serialno"},
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
                    "arguments": {"command": "adb shell getprop ro.serialno"},
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
                "run_adb_shell", {"command": "adb shell getprop ro.boot.serialno"}
            )
            assert not result.isError and verify_text_result(result) == "ro.boot.serialno\r\n"
            output_id = latest_record(tmp_path)["output_id"]
    record_path = tmp_path / "outputs" / output_id / "record.json"
    record = json.loads(record_path.read_text())
    record["argv"] = ["dumpsys", "display"]
    record_path.write_text(json.dumps(record))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page = await session.call_tool("read_command_output", {"output_id": output_id})
            assert page.isError and "current allowlist" in page.content[0].text
    assert record_path.exists()
