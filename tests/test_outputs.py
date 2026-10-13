import json

import pytest
from conftest import calls
from pydantic import ValidationError

from adb_mcp.adb import ADBService
from adb_mcp.commands import OutputRequest
from adb_mcp.outputs import OutputStore


async def test_long_parallel_streams_restart_pagination(config, fake_adb, tmp_path):
    result = await ADBService(config).run(["getprop", "ro.test.long"])
    assert result.error_code is None and result.capture_complete
    assert result.stdout_truncated and result.stderr_truncated
    assert len(result.stdout) == len(result.stderr) == 20
    assert result.stderr_bytes == 200000
    expected = "中😀e\u0301\r\n" * 20000
    assert result.stdout_bytes == len(expected.encode())
    # A separate store instance has no in-memory state from the execution.
    store = OutputStore(config.output_directory, 20, config.read_max_chars)
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


async def test_long_single_line(config, fake_adb):
    result = await ADBService(config).run(["getprop", "ro.test.single_line"])
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


async def test_capture_limit_stops_and_marks_partial(config, fake_adb, tmp_path):
    config.max_capture_bytes = 128
    result = await ADBService(config).run(["getprop", "ro.test.limit"])
    assert result.error_code == "capture_limit"
    assert not result.capture_complete and result.exit_code is None
    assert result.stdout_bytes + result.stderr_bytes == 128
    assert all(process.returncode is not None for process in fake_adb)
    page = ADBService(config).store.read(OutputRequest(output_id=result.output_id))
    assert len(page.text) == 128 and page.error_code == "capture_limit"
    assert not page.capture_complete
    assert sum("shell" in args for args in calls(tmp_path)) == 1


async def test_exact_capture_limit_is_complete(config, fake_adb):
    config.max_capture_bytes = 128
    result = await ADBService(config).run(["getprop", "ro.test.exact_limit"])
    assert result.error_code is None and result.stdout_bytes == 128
    assert result.capture_complete


@pytest.mark.parametrize(
    ("key", "errors"),
    [
        ("ro.test.invalid_utf8", True),
        ("ro.test.literal_replacement", False),
    ],
)
async def test_decoding_status_is_not_inferred_from_replacement_character(
    config, fake_adb, key, errors
):
    result = await ADBService(config).run(["getprop", key])
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
    result = await service.run(["getprop", "ro.test.normal"])
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
    result = await service.run(["getprop"])
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
    result = await ADBService(config).run(["getprop"])
    assert result.error_code == "storage_failed" and result.output_id is None
    assert fake_adb == []
    assert config.output_directory.read_bytes() == b"preserve this file"


async def test_partial_page_retains_source_error(config, fake_adb):
    result = await ADBService(config).run(["getprop", "ro.test.fail"])
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
