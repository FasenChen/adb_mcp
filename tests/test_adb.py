import asyncio
import json

import anyio
import pytest
from conftest import calls

from adb_mcp.adb import ADBService
from adb_mcp.commands import CommandError
from adb_mcp.config import NetworkTarget
from adb_mcp.outputs import Capture


@pytest.mark.parametrize(
    ("key", "code", "stdout", "stderr"),
    [
        ("ro.test.normal", 0, "ro.test.normal\r\n", ""),
        ("ro.test.empty", 0, "", ""),
        ("ro.test.fail", 7, "partial\r\n", "remote error\n"),
    ],
)
async def test_real_streams_exit_codes(config, fake_adb, key, code, stdout, stderr):
    result = await ADBService(config).run(["getprop", key])
    assert result.exit_code == code
    assert (result.stdout, result.stderr) == (stdout, stderr)
    assert result.capture_complete
    assert result.error_code == (None if code == 0 else "command_failed")
    assert result.target_serial == "test-device"
    assert result.output_id
    assert all(process.returncode is not None for process in fake_adb)


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("absent", "device_not_found"),
        ("offline", "device_offline"),
        ("unauthorized", "device_unauthorized"),
        ("ambiguous", "device_ambiguous"),
        ("old_shell", "shell_protocol_unsupported"),
    ],
)
async def test_exact_device_no_fallback(config, fake_adb, monkeypatch, tmp_path, mode, error):
    monkeypatch.setenv("FAKE_ADB_MODE", mode)
    result = await ADBService(config).run(["getprop"])
    assert result.error_code == error
    assert result.exit_code is None and not result.capture_complete
    assert not any("shell" in args for args in calls(tmp_path))
    assert not any("connect" in args for args in calls(tmp_path))


async def test_network_connect_once(config, fake_adb, monkeypatch, tmp_path):
    config.target = NetworkTarget(host="192.0.2.1", port=5555)
    monkeypatch.setenv("FAKE_ADB_SERIAL", config.target_serial)
    monkeypatch.setenv("FAKE_ADB_MODE", "connect")
    service = ADBService(config)
    for _ in range(2):
        result = await service.run(["getprop"])
        assert result.error_code is None
    log = calls(tmp_path)
    assert sum(args == ["connect", config.target_serial] for args in log) == 1
    assert all(args[1] == config.target_serial for args in log if args[0] == "-s")


async def test_connect_failure_and_auto_connect_disabled(config, fake_adb, monkeypatch, tmp_path):
    config.target = NetworkTarget(host="192.0.2.1", port=5555)
    monkeypatch.setenv("FAKE_ADB_SERIAL", config.target_serial)
    monkeypatch.setenv("FAKE_ADB_MODE", "connect_fail")
    assert (await ADBService(config).run(["getprop"])).error_code == "connect_failed"
    config.auto_connect = False
    before = len(calls(tmp_path))
    assert (await ADBService(config).run(["getprop"])).error_code == "device_not_found"
    assert calls(tmp_path)[before:] == [["devices", "-l"]]


async def test_timeout_retains_output_without_replay(config, fake_adb, tmp_path):
    config.command_timeout_seconds = 0.25
    result = await ADBService(config).run(["getprop", "ro.test.slow"])
    assert result.error_code == "command_timeout"
    assert not result.capture_complete and result.exit_code is None
    assert result.stdout == "before timeout\r\n"
    assert sum("shell" in args for args in calls(tmp_path)) == 1
    assert all(process.returncode is not None for process in fake_adb)


async def test_connection_total_timeout(config, fake_adb, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_MODE", "connect_slow")
    config.connect_timeout_seconds = 0.1
    result = await ADBService(config).run(["getprop"])
    assert result.error_code == "connect_timeout"
    assert all(process.returncode is not None for process in fake_adb)


async def wait_for_query(tmp_path):
    async with asyncio.timeout(3):
        # Simulator is a separate executable; it cannot signal an in-process Event.
        while not any("shell" in args for args in calls(tmp_path)):  # noqa: ASYNC110
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)


async def test_cancel_retains_record_and_reaps(config, fake_adb, tmp_path):
    service = ADBService(config)
    task = asyncio.create_task(service.run(["getprop", "ro.test.slow"]))
    await wait_for_query(tmp_path)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    records = list(config.output_directory.glob("*/record.json"))
    assert len(records) == 1
    result = json.loads(records[0].read_text())
    assert result["error_code"] == "cancelled" and not result["capture_complete"]
    assert result["stdout"] == "before timeout\r\n"
    assert all(process.returncode is not None for process in fake_adb)
    assert (await service.run(["getprop", "ro.test.empty"])).error_code is None


async def test_anyio_cancel_scope_preserves_cleanup(config, fake_adb, tmp_path):
    with anyio.move_on_after(0.3) as scope:
        await ADBService(config).run(["getprop", "ro.test.slow"])
    assert scope.cancel_called
    assert all(process.returncode is not None for process in fake_adb)
    record = json.loads(next(config.output_directory.glob("*/record.json")).read_text())
    assert record["error_code"] == "cancelled"


async def test_serial_execution(config, fake_adb, tmp_path):
    service = ADBService(config)
    await asyncio.gather(*(service.run(["getprop", f"ro.test.{i}"]) for i in range(3)))
    assert [args[0] for args in calls(tmp_path)] == ["devices", "-s", "-s"] * 3


async def test_disk_write_failure_stops_query(config, fake_adb, monkeypatch):
    def fail(*args):
        raise OSError("simulated disk full")

    monkeypatch.setattr(Capture, "write", fail)
    result = await ADBService(config).run(["getprop", "ro.test.slow"])
    assert result.error_code == "storage_failed"
    assert result.output_id is None
    assert not result.capture_complete
    assert all(process.returncode is not None for process in fake_adb)


async def test_record_commit_failure_has_no_readable_id(config, fake_adb, monkeypatch):
    service = ADBService(config)

    def fail(*args):
        raise OSError("simulated metadata write error")

    monkeypatch.setattr(service.store, "finish", fail)
    result = await service.run(["getprop"])
    assert result.error_code == "storage_failed" and result.output_id is None


async def test_unavailable_adb(config):
    config.adb_path = "nonexistent-adb-executable"
    result = await ADBService(config).run(["getprop"])
    assert result.error_code == "adb_unavailable"


async def test_invalid_command_starts_no_process(config, fake_adb):
    with pytest.raises(CommandError):
        await ADBService(config).run(["settings", "put", "global", "x", "1"])
    assert fake_adb == []


async def test_disconnect_after_preparation_is_error_without_retry(
    config, fake_adb, monkeypatch, tmp_path
):
    monkeypatch.setenv("FAKE_ADB_MODE", "disconnect_during_query")
    result = await ADBService(config).run(["getprop"])
    assert result.error_code == "command_failed" and result.exit_code == 1
    assert result.stdout == "" and result.stderr.startswith("adb:")
    assert sum("shell" in args for args in calls(tmp_path)) == 1
    assert not any("connect" in args for args in calls(tmp_path))


async def test_cleanup_failure_blocks_following_operations(config, fake_adb, monkeypatch):
    original = asyncio.create_subprocess_exec
    stubborn = []

    class StubbornProcess:
        def __init__(self, process):
            self.process = process
            self.stdout, self.stderr = process.stdout, process.stderr

        @property
        def returncode(self):
            return self.process.returncode

        def kill(self):
            # Simulate a local process that does not exit when terminated.
            pass

        async def wait(self):
            return await self.process.wait()

    async def create(program, *args, **kwargs):
        process = await original(program, *args, **kwargs)
        if "shell" in args:
            stubborn.append(process)
            return StubbornProcess(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    config.command_timeout_seconds = 0.15
    config.cleanup_timeout_seconds = 0.1
    service = ADBService(config)
    try:
        result = await service.run(["getprop", "ro.test.slow"])
        assert result.error_code == "cleanup_failed" and not result.capture_complete
        before = len(fake_adb)
        following = await service.run(["getprop", "ro.test.empty"])
        assert following.error_code == "cleanup_failed" and len(fake_adb) == before
    finally:
        for process in stubborn:
            process.kill()
            await process.wait()


async def test_cancel_during_process_creation_reaps_handle(config, fake_adb, monkeypatch):
    original = asyncio.create_subprocess_exec
    creating = asyncio.Event()
    released = asyncio.Event()

    async def create(program, *args, **kwargs):
        process = await original(program, *args, **kwargs)
        if "shell" in args:
            creating.set()
            await released.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    task = asyncio.create_task(ADBService(config).run(["getprop", "ro.test.slow"]))
    await asyncio.wait_for(creating.wait(), 3)
    task.cancel("original cancellation")
    released.set()
    with pytest.raises(asyncio.CancelledError, match="original cancellation"):
        await task
    assert all(process.returncode is not None for process in fake_adb)
