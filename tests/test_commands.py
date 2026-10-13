import shlex
from itertools import product
from typing import get_args

import pytest

from adb_mcp.commands import (
    COMMANDS,
    Command,
    CommandError,
    Query,
    resolve_query,
    shell_command,
    validate_command,
)


@pytest.mark.parametrize(
    ("query", "argv"),
    [
        ("clientidbase", ["getprop", "ro.com.google.clientidbase"]),
        ("boot_serialno", ["getprop", "ro.boot.serialno"]),
        ("serialno", ["getprop", "ro.serialno"]),
        ("oem_key1", ["getprop", "ro.oem.key1"]),
        ("youtube_cert_scope", ["getprop", "ro.vendor.youtube.cert_scope"]),
        ("test_harness_state", ["getprop", "persist.sys.test_harness"]),
        ("features", ["pm", "list", "features"]),
        ("android_id", ["settings", "get", "secure", "android_id"]),
        ("interfaces", ["ifconfig"]),
        ("addresses", ["ip", "addr"]),
    ],
)
def test_allowed_commands_and_posix_quoting(query, argv):
    resolved = resolve_query(argv[0], query)
    assert resolved == argv
    assert validate_command(argv) == argv
    assert shlex.split(shell_command(argv)) == argv
    resolved.append("extra")
    assert resolve_query(argv[0], query) == argv


def test_enum_and_mapping_cover_same_queries():
    assert set(get_args(Query)) == set(COMMANDS)
    assert set(get_args(Command)) == {argv[0] for argv in COMMANDS.values()}


@pytest.mark.parametrize(
    ("command", "query"),
    [
        (command, query)
        for command, query in product(get_args(Command), get_args(Query))
        if COMMANDS[query][0] != command
    ],
)
def test_mismatched_category_is_rejected(command, query):
    with pytest.raises(CommandError, match="command/query"):
        resolve_query(command, query)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["getprop"],
        ["getprop", "vendor.new_property"],
        ["settings", "list", "secure"],
        ["settings", "get", "global", "device_name"],
        ["settings", "get", "secure", "other_key"],
        ["settings", "get", "system", "合法 空格'\"键😀"],
        ["dumpsys", "display"],
        ["pm", "list", "features", "|", "grep", "com.google.android.tv"],
        ["pm", "list", "packages"],
        ["pm", "clear", "com.example"],
        ["ifconfig", "wlan0", "down"],
        ["ip", "addr", "flush", "dev", "wlan0"],
        ["cmd", "testharness", "enable"],
        ["am", "start", "-a", "android.settings.SETTINGS"],
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
