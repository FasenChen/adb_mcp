import shlex

import pytest

from adb_mcp.commands import CommandError, shell_command, validate_command


@pytest.mark.parametrize(
    "argv",
    [
        ["getprop"],
        ["getprop", "vendor.new_property"],
        ["settings", "list", "secure"],
        ["settings", "get", "global", "device_name"],
        ["settings", "get", "system", "合法 空格'\"键😀"],
        ["dumpsys", "display"],
    ],
)
def test_allowed_commands_and_posix_quoting(argv):
    assert validate_command(argv) == argv
    assert shlex.split(shell_command(argv)) == argv


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["sh", "-c", "getprop"],
        ["su", "0", "getprop"],
        ["getprop", "x", "default"],
        ["getprop", "--help"],
        ["getprop", "a;b"],
        ["getprop", "$(id)"],
        ["getprop", "`id`"],
        ["getprop", "a|id"],
        ["getprop", "x\n"],
        ["getprop", "x\x00"],
        ["getprop", "x\u202e"],
        ["settings", "put", "global", "x", "1"],
        ["settings", "delete", "global", "x"],
        ["settings", "get", "bad", "x"],
        ["settings", "get", "system", ""],
        ["settings", "get", "system", "--help"],
        ["settings", "list", "secure", "extra"],
        ["settings", "get", "global", "x&&id"],
        ["dumpsys", "display", "set-brightness", "1"],
        ["dumpsys", "battery", "reset"],
        ["dumpsys", "display", "--proto"],
        ["getprop", "a" * 4097],
        ["getprop"] * 33,
        [1],
        "getprop",
    ],
)
def test_reject_before_submission(argv):
    with pytest.raises(CommandError) as error:
        validate_command(argv)
    assert error.value.code == "invalid_command"
