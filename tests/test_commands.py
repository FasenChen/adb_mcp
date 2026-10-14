import shlex

import pytest
from conftest import ALLOWED_COMMANDS

from adb_mcp.commands import CommandError, command_args, resolve_command, shell_command


@pytest.mark.parametrize("command", ALLOWED_COMMANDS)
def test_allowed_commands_and_posix_quoting(command):
    argv = resolve_command(command, ALLOWED_COMMANDS)
    assert argv == shlex.split(command)[2:]
    assert shlex.split(shell_command(argv)) == argv


@pytest.mark.parametrize(
    "command",
    [
        "adb shell ifconfig ",
        " adb shell ifconfig",
        "adb  shell ifconfig",
        "adb shell  ifconfig",
        "adb shell IFCONFIG",
        "adb shell 'ifconfig'",
        "adb shell ifconfig\n",
        "adb shell ifconfig\x00",
        "adb shell ifconfig wlan0",
        "adb shell ifconfig; reboot",
        "adb shell ifconfig && reboot",
        "adb shell pm list features | grep android",
        "adb shell getprop $(id)",
        "adb shell getprop vendor.new_property",
        "adb -s other-device shell ifconfig",
        "ifconfig",
        "",
        ["ifconfig"],
        1,
    ],
)
def test_only_exact_configured_strings_are_accepted(command):
    with pytest.raises(CommandError, match="exactly match"):
        resolve_command(command, ALLOWED_COMMANDS)


def test_whitelist_controls_commands_without_code_mapping():
    command = "adb shell getprop vendor.new_property"
    assert resolve_command(command, [command]) == ["getprop", "vendor.new_property"]
    assert resolve_command("adb shell  ifconfig ", ["adb shell  ifconfig "]) == ["ifconfig"]


def test_configured_quoted_argument_is_preserved():
    command = "adb shell settings get system 'key with spaces'"
    argv = command_args(command)
    assert argv == ["settings", "get", "system", "key with spaces"]
    assert shlex.split(shell_command(argv)) == argv
