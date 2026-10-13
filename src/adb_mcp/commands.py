"""Command policy and the public evidence contract."""

import re
import shlex
import unicodedata
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


class ShellRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    argv: Annotated[list[str], Field(min_length=1, max_length=32)]


def validate_command(argv: list[str]) -> list[str]:
    try:
        argv = ShellRequest(argv=argv).argv
    except ValueError as exc:
        raise CommandError("invalid_command", "argv must contain 1–32 strings") from exc
    if sum(len(arg) for arg in argv) > 4096:
        raise CommandError("invalid_command", "argv exceeds 4096 characters")
    if any(any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in arg) for arg in argv):
        raise CommandError("invalid_command", "control characters are prohibited")
    if any(any(char in arg for char in ";|&<>`$\\") for arg in argv):
        raise CommandError("invalid_command", "shell operators and substitutions are prohibited")

    valid = False
    if argv[0] == "getprop":
        valid = len(argv) == 1 or (
            len(argv) == 2
            and re.fullmatch(r"[A-Za-z0-9_.-]+", argv[1]) is not None
            and not argv[1].startswith("-")
        )
    elif argv[0] == "settings":
        # No --user, put, delete, reset, or unknown option combinations in v1.
        if len(argv) >= 3 and argv[2] in {"global", "system", "secure"}:
            valid = (argv[1] == "list" and len(argv) == 3) or (
                argv[1] == "get"
                and len(argv) == 4
                and bool(argv[3])
                and not argv[3].startswith("-")
            )
    elif argv == ["dumpsys", "display"]:
        valid = True
    if not valid:
        raise CommandError("invalid_command", "unsupported read-only command/argument combination")
    return argv.copy()


def shell_command(argv: list[str]) -> str:
    """ADB joins shell arguments; send one POSIX-quoted remote command string."""
    return shlex.join(argv)


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["adb-command/v1"] = "adb-command/v1"
    output_id: str | None = None
    target_serial: str
    argv: list[str]
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
