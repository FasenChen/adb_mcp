"""Exact command allowlist and local evidence models."""

import shlex
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ErrorCode = Literal[
    "invalid_command",
    "device_not_found",
    "device_offline",
    "device_unauthorized",
    "device_ambiguous",
    "device_not_ready",
    "connect_failed",
    "connect_timeout",
    "shell_protocol_unsupported",
    "adb_unavailable",
    "adb_error",
    "command_failed",
    "command_timeout",
    "cancelled",
    "capture_limit",
    "capture_failed",
    "storage_failed",
    "cleanup_failed",
]
Stream = Literal["stdout", "stderr"]


class CommandError(Exception):
    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code


def command_args(command: str) -> list[str]:
    """Parse configured commands; execution never uses a local shell."""
    if not command.startswith("adb shell ") or any(ord(char) < 32 for char in command):
        raise ValueError("commands must start with 'adb shell ' and contain no control characters")
    if any(char in command for char in ";|&<>`$"):
        raise ValueError("shell operators and substitutions are not supported")
    argv = shlex.split(command)[2:]
    if not argv or not argv[0] or argv[0].startswith("-"):
        raise ValueError("commands must include a device-side executable, not ADB options")
    return argv


def resolve_command(command: str, allowed_commands: list[str]) -> list[str]:
    if not isinstance(command, str) or command not in allowed_commands:
        raise CommandError(
            "invalid_command", "command does not exactly match the configured allowlist"
        )
    return command_args(command)


def shell_command(argv: list[str]) -> str:
    """ADB joins shell arguments; send one POSIX-quoted remote command string."""
    return shlex.join(argv)


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["adb-command/v1"] = "adb-command/v1"
    output_id: str | None = None
    target_serial: str
    argv: list[str]
    command: str | None = None
    started_at: str
    finished_at: str
    duration_ms: int
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    capture_complete: bool = False
    decoding_errors: bool = False
    error_code: ErrorCode | None = None
    error_message: str | None = None


class OutputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    output_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    stream: Stream = "stdout"
    start_char: Annotated[int, Field(ge=0)] = 0
    max_chars: Annotated[int, Field(ge=1)] = 6000


class OutputPage(BaseModel):
    schema_version: Literal["adb-output-page/v1"] = "adb-output-page/v1"
    output_id: str
    stream: Stream
    start_char: int
    text: str
    next_char: int
    eof: bool
    total_chars: int
    capture_complete: bool
    decoding_errors: bool
    error_code: ErrorCode | None
