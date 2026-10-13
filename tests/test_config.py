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
