"""Strict configuration; relative paths are anchored to the config file."""

import ipaddress
import json
import re
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .commands import command_args


class USBTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    serial: Annotated[str, Field(min_length=1, max_length=256)]

    @field_validator("serial")
    @classmethod
    def validate_serial(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value) or value.startswith("-"):
            raise ValueError("USB serial must be an exact adb devices identifier")
        return value


class NetworkTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    host: Annotated[str, Field(min_length=1, max_length=253)]
    port: Annotated[int, Field(ge=1, le=65535)] = 5555

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            if not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*",
                value,
            ):
                raise ValueError("host must be an IP address or DNS hostname") from None
            return value


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    adb_path: str = "adb"
    target: USBTarget | NetworkTarget
    allowed_commands: list[str] = Field(default_factory=list)
    auto_connect: bool = True
    connect_timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 10
    command_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 30
    cleanup_timeout_seconds: Annotated[float, Field(gt=0, le=30)] = 5
    preview_max_chars_per_stream: Annotated[int, Field(ge=1, le=24000)] = 6000
    read_max_chars: Annotated[int, Field(ge=1, le=48000)] = 12000
    max_capture_bytes: Annotated[int, Field(ge=1, le=268435456)] = 16777216
    output_directory: Path = Path("data/command-outputs")

    @field_validator("allowed_commands")
    @classmethod
    def validate_allowed_commands(cls, values: list[str]) -> list[str]:
        for command in values:
            command_args(command)
        return values

    @field_validator("adb_path")
    @classmethod
    def validate_adb_path(cls, value: str) -> str:
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("adb_path must be a nonempty executable path")
        return value

    @property
    def target_serial(self) -> str:
        if isinstance(self.target, USBTarget):
            return self.target.serial
        host = self.target.host
        return f"[{host}]:{self.target.port}" if ":" in host else f"{host}:{self.target.port}"


def load_config(path: Path) -> Config:
    path = path.resolve(strict=True)
    values = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(values, dict):
        raise ValueError("config must be a JSON object")
    # Paths are parsed explicitly before strict model validation.
    output = values.get("output_directory", "data/command-outputs")
    if not isinstance(output, str) or not output:
        raise ValueError("output_directory must be a nonempty path string")
    values["output_directory"] = (path.parent / output).resolve()
    config = Config.model_validate(values)
    executable = Path(config.adb_path)
    if executable.is_absolute() or "/" in config.adb_path or "\\" in config.adb_path:
        config.adb_path = str((path.parent / executable).resolve())
    return config
