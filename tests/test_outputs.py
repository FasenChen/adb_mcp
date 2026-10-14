import json

import pytest
from conftest import calls
from pydantic import ValidationError

from adb_mcp.adb import ADBService
from adb_mcp.commands import OutputRequest
from adb_mcp.outputs import OutputStore


async def test_long_parallel_streams_restart_pagination(config, fake_adb, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_QUERY_MODE", "long")
    result = await ADBService(config).run("adb shell getprop ro.boot.serialno")
    assert result.error_code is None and result.capture_complete
    assert result.stdout_truncated and result.stderr_truncated
    assert len(result.stdout) == len(result.stderr) == 20
    assert result.stderr_bytes == 200000
    expected = "中😀e\u0301\r\n" * 20000
    assert result.stdout_bytes == len(expected.encode())
    # A separate store instance has no in-memory state from the execution.
    store = OutputStore(config.output_directory, 20, config.read_max_chars, config.allowed_commands)
    observed = []
    start = 0
    while True:
        page = store.read(
            OutputRequest(
                output_id=result.output_id,
                start_char=start,
                max_chars=997,
            )
        )
        assert page.total_chars == len(expected)
        assert page.next_char == start + len(page.text)
        observed.append(page.text)
        if page.eof:
            break
        start = page.next_char
    assert "".join(observed) == expected
    assert (
        store.read(OutputRequest(output_id=result.output_id, start_char=len(expected))).text == ""
    )
    assert sum("shell" in args for args in calls(tmp_path)) == 1
    assert (
        config.output_directory / result.output_id / "stdout.bin"
    ).read_bytes() == expected.encode()


async def test_long_single_line(config, fake_adb, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_QUERY_MODE", "single_line")
    result = await ADBService(config).run("adb shell getprop ro.boot.serialno")
    assert result.error_code is None
    assert result.stdout_truncated and "\n" not in result.stdout
    page = ADBService(config).store.read(
        OutputRequest(
            output_id=result.output_id,
            start_char=199995,
            max_chars=10,
        )
    )
    assert page.total_chars == 200000 and len(page.text) == 5 and page.eof


async def test_capture_limit_stops_and_marks_partial(config, fake_adb, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_QUERY_MODE", "limit")
    config.max_capture_bytes = 128
    result = await ADBService(config).run("adb shell getprop ro.boot.serialno")
    assert result.error_code == "capture_limit"
    assert not result.capture_complete and result.exit_code is None
    assert result.stdout_bytes + result.stderr_bytes == 128
    assert all(process.returncode is not None for process in fake_adb)
    page = ADBService(config).store.read(OutputRequest(output_id=result.output_id))
    assert len(page.text) == 128 and page.error_code == "capture_limit"
    assert not page.capture_complete
    assert sum("shell" in args for args in calls(tmp_path)) == 1


async def test_exact_capture_limit_is_complete(config, fake_adb, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_QUERY_MODE", "exact_limit")
    config.max_capture_bytes = 128
    result = await ADBService(config).run("adb shell getprop ro.boot.serialno")
    assert result.error_code is None and result.stdout_bytes == 128
    assert result.capture_complete


@pytest.mark.parametrize(
    ("mode", "errors"),
    [
        ("invalid_utf8", True),
        ("literal_replacement", False),
    ],
)
async def test_decoding_status_is_not_inferred_from_replacement_character(
    config, fake_adb, mode, errors, monkeypatch
):
    monkeypatch.setenv("FAKE_ADB_QUERY_MODE", mode)
    result = await ADBService(config).run("adb shell getprop ro.boot.serialno")
    assert result.decoding_errors is errors and result.capture_complete
    assert (
        ADBService(config)
        .store.read(
            OutputRequest(
                output_id=result.output_id,
            )
        )
        .decoding_errors
        is errors
    )


async def test_invalid_pagination_and_ids(config, fake_adb):
    service = ADBService(config)
    result = await service.run("adb shell getprop ro.boot.serialno")
    with pytest.raises(ValueError, match="exceeds"):
        service.store.read(OutputRequest(output_id=result.output_id, start_char=100))
    with pytest.raises(ValueError, match="must not exceed"):
        service.store.read(OutputRequest(output_id=result.output_id, max_chars=12001))
    for values in [
        {"start_char": -1},
        {"start_char": True},
        {"max_chars": "3"},
        {"output_id": "../../config.json"},
        {"stream": "merged"},
    ]:
        with pytest.raises(ValidationError):
            OutputRequest.model_validate({"output_id": result.output_id} | values)
    with pytest.raises(ValueError, match="not found"):
        service.store.read(OutputRequest(output_id="0" * 32))


async def test_uncommitted_or_damaged_record_is_not_complete(config, fake_adb):
    service = ADBService(config)
    result = await service.run("adb shell getprop ro.boot.serialno")
    directory = config.output_directory / result.output_id
    (directory / "stdout.bin").write_bytes(b"damaged")
    with pytest.raises(ValueError, match="size mismatch"):
        service.store.read(OutputRequest(output_id=result.output_id))
    (directory / "record.json").unlink()
    with pytest.raises(ValueError, match="not committed"):
        service.store.read(OutputRequest(output_id=result.output_id))


async def test_disk_directory_error_has_no_record(config, fake_adb, tmp_path):
    config.output_directory = tmp_path / "not-a-directory"
    config.output_directory.write_bytes(b"preserve this file")
    result = await ADBService(config).run("adb shell getprop ro.boot.serialno")
    assert result.error_code == "storage_failed" and result.output_id is None
    assert fake_adb == []
    assert config.output_directory.read_bytes() == b"preserve this file"


async def test_partial_page_retains_source_error(config, fake_adb, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_QUERY_MODE", "fail")
    result = await ADBService(config).run("adb shell getprop ro.boot.serialno")
    page = ADBService(config).store.read(
        OutputRequest(
            output_id=result.output_id,
            stream="stderr",
            max_chars=3,
        )
    )
    assert page.text == "rem" and page.error_code == "command_failed"
    assert page.capture_complete and not page.eof
    manifest = json.loads((config.output_directory / result.output_id / "record.json").read_text())
    assert manifest["stdout"] == "partial\r\n"


@pytest.mark.parametrize(
    "argv",
    [
        ["getprop"],
        ["getprop", "vendor.new_property"],
        ["settings", "list", "secure"],
        ["settings", "get", "global", "device_name"],
        ["dumpsys", "display"],
        ["cmd", "testharness", "enable"],
    ],
)
async def test_saved_output_rechecked_against_current_policy(config, fake_adb, tmp_path, argv):
    result = await ADBService(config).run("adb shell getprop ro.boot.serialno")
    directory = config.output_directory / result.output_id
    record_path = directory / "record.json"
    record = json.loads(record_path.read_text())
    record["argv"] = argv
    record_path.write_text(json.dumps(record))
    before = calls(tmp_path)
    for stream in ("stdout", "stderr"):
        with pytest.raises(ValueError, match="current allowlist"):
            OutputStore(
                config.output_directory, 20, config.read_max_chars, config.allowed_commands
            ).read(OutputRequest(output_id=result.output_id, stream=stream))
        assert (directory / f"{stream}.bin").exists()
    assert calls(tmp_path) == before
    assert json.loads(record_path.read_text())["argv"] == argv


async def test_command_revocation_preserves_files_and_blocks_reads(config, fake_adb):
    command = "adb shell getprop ro.boot.serialno"
    result = await ADBService(config).run(command)
    # Same argv with different whitespace is a different authorization for new records.
    config.allowed_commands = ["adb shell  getprop ro.boot.serialno"]
    service = ADBService(config)
    with pytest.raises(ValueError, match="current allowlist"):
        service.store.read(OutputRequest(output_id=result.output_id))
    assert (config.output_directory / result.output_id / "stdout.bin").exists()


async def test_legacy_argv_record_remains_readable_when_allowed(config, fake_adb):
    result = await ADBService(config).run("adb shell getprop ro.boot.serialno")
    path = config.output_directory / result.output_id / "record.json"
    record = json.loads(path.read_text())
    del record["command"]
    path.write_text(json.dumps(record))
    assert ADBService(config).store.read(OutputRequest(output_id=result.output_id)).text == (
        "ro.boot.serialno\r\n"
    )
