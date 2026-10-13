"""Command policy and the public evidence contract."""

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
Command = Literal["getprop", "pm", "settings", "ifconfig", "ip"]
Query = Literal[
    "clientidbase",
    "boot_serialno",
    "serialno",
    "oem_key1",
    "youtube_cert_scope",
    "test_harness_state",
    "features",
    "android_id",
    "interfaces",
    "addresses",
]
COMMANDS: dict[Query, list[str]] = {
    "clientidbase": ["getprop", "ro.com.google.clientidbase"],
    "boot_serialno": ["getprop", "ro.boot.serialno"],
    "serialno": ["getprop", "ro.serialno"],
    "oem_key1": ["getprop", "ro.oem.key1"],
    "youtube_cert_scope": ["getprop", "ro.vendor.youtube.cert_scope"],
    "test_harness_state": ["getprop", "persist.sys.test_harness"],
    "features": ["pm", "list", "features"],
    "android_id": ["settings", "get", "secure", "android_id"],
    "interfaces": ["ifconfig"],
    "addresses": ["ip", "addr"],
}


class CommandError(Exception):
    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code


def resolve_query(command: Command, query: Query) -> list[str]:
    argv = COMMANDS.get(query)
    if argv is None or argv[0] != command:
        raise CommandError("invalid_command", "unsupported command/query combination")
    return argv.copy()


def validate_command(argv: list[str]) -> list[str]:
    if not isinstance(argv, list) or argv not in COMMANDS.values():
        raise CommandError("invalid_command", "command is outside the fixed read-only query list")
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
