import asyncio
import json
import sys
from pathlib import Path

import pytest

from adb_mcp.config import Config

ALLOWED_COMMANDS = [
    "adb shell getprop ro.com.google.clientidbase",
    "adb shell getprop ro.boot.serialno",
    "adb shell getprop ro.serialno",
    "adb shell getprop ro.oem.key1",
    "adb shell getprop ro.vendor.youtube.cert_scope",
    "adb shell getprop persist.sys.test_harness",
    "adb shell pm list features",
    "adb shell settings get secure android_id",
    "adb shell ifconfig",
    "adb shell ip addr",
]

SIMULATOR = Path(__file__).with_name("fake_adb.py")


@pytest.fixture
def fake_adb(monkeypatch, tmp_path):
    original = asyncio.create_subprocess_exec
    processes = []

    async def create(program, *args, **kwargs):
        if program == "fake-adb":
            process = await original(sys.executable, str(SIMULATOR), *args, **kwargs)
            processes.append(process)
            return process
        return await original(program, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setenv("FAKE_ADB_LOG", str(tmp_path / "adb.log"))
    monkeypatch.setenv("FAKE_ADB_CONNECTED", str(tmp_path / "connected"))
    monkeypatch.setenv("FAKE_ADB_SERIAL", "test-device")
    monkeypatch.setenv("FAKE_ADB_MODE", "normal")
    monkeypatch.setenv("FAKE_ADB_QUERY_MODE", "normal")
    monkeypatch.delenv("FAKE_ADB_QUERY_MODES", raising=False)
    return processes


@pytest.fixture
def config(tmp_path):
    return Config(
        adb_path="fake-adb",
        allowed_commands=ALLOWED_COMMANDS.copy(),
        target={"serial": "test-device"},
        output_directory=tmp_path / "outputs",
        preview_max_chars_per_stream=20,
        read_max_chars=12000,
        command_timeout_seconds=2.0,
        connect_timeout_seconds=2.0,
        cleanup_timeout_seconds=1.0,
    )


def calls(tmp_path):
    log = tmp_path / "adb.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
