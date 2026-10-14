import json

import pytest
from pydantic import ValidationError

from adb_mcp.config import Config, load_config


@pytest.mark.parametrize(
    "target",
    [
        {"serial": "test", "host": "192.0.2.1"},
        {},
        {"host": "bad;host"},
        {"host": "192.0.2.1", "port": True},
        {"host": "192.0.2.1", "port": "5555"},
        {"host": "192.0.2.1", "port": 65536},
        {"serial": "bad serial"},
    ],
)
def test_strict_target(target):
    with pytest.raises(ValidationError):
        Config(target=target)


def test_relative_config_paths_and_ipv6(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "target": {"host": "2001:db8::1", "port": 5555},
                "adb_path": "tools/adb",
                "output_directory": "./records",
            }
        )
    )
    config = load_config(path)
    assert config.target_serial == "[2001:db8::1]:5555"
    assert config.output_directory == tmp_path / "records"
    assert config.adb_path == str(tmp_path / "tools" / "adb")


def test_example_template_is_valid_after_host_substitution(tmp_path):
    from pathlib import Path

    template = Path(__file__).parents[1] / "config.example.json"
    path = tmp_path / "config.json"
    path.write_text(template.read_text().replace("<TV_IP>", "192.0.2.1"))
    assert load_config(path).target_serial == "192.0.2.1:5555"


@pytest.mark.parametrize("adb_path", ["./adb.exe", ".\\adb.exe", "tools/adb.exe"])
def test_explicit_adb_path_is_relative_to_config_not_cwd(tmp_path, monkeypatch, adb_path):
    config_directory = tmp_path / "settings"
    working_directory = tmp_path / "working"
    config_directory.mkdir()
    working_directory.mkdir()
    path = config_directory / "config.json"
    path.write_text(json.dumps({"target": {"serial": "test"}, "adb_path": adb_path}))
    monkeypatch.chdir(working_directory)
    assert load_config(path).adb_path == str((config_directory / adb_path).resolve())


@pytest.mark.parametrize("adb_path", ["adb", "adb.exe"])
def test_bare_adb_command_keeps_path_lookup(tmp_path, adb_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"target": {"serial": "test"}, "adb_path": adb_path}))
    assert load_config(path).adb_path == adb_path


def test_absolute_adb_path_keeps_selected_executable(tmp_path):
    executable = (tmp_path / "tools" / "adb.exe").resolve()
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"target": {"serial": "test"}, "adb_path": str(executable)}))
    assert load_config(path).adb_path == str(executable)


@pytest.mark.parametrize(
    "commands",
    [
        "adb shell ifconfig",
        [1],
        ["ifconfig"],
        ["adb shell"],
        ["adb shell "],
        ["adb shell -s other-device ifconfig"],
        ["adb shell ifconfig\n"],
        ["adb shell ifconfig\x00"],
        ["adb shell ifconfig; reboot"],
        ["adb shell ifconfig | cat"],
        ["adb shell getprop $(id)"],
        ["adb shell getprop 'unterminated"],
    ],
)
def test_invalid_allowlist_is_rejected(commands):
    with pytest.raises(ValidationError):
        Config(target={"serial": "test"}, allowed_commands=commands)


def test_allowlist_preserves_exact_strings_and_defaults_to_deny():
    command = "adb shell  ifconfig "
    assert Config(target={"serial": "test"}, allowed_commands=[command]).allowed_commands == [
        command
    ]
    assert Config(target={"serial": "test"}).allowed_commands == []
