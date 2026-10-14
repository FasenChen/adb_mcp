"""Bounded binary capture and restart-safe Unicode-code-point pagination."""

import codecs
import json
import os
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from .commands import (
    CommandError,
    CommandResult,
    OutputPage,
    OutputRequest,
    Stream,
    command_args,
    resolve_command,
)


def scan_text(path: Path, start: int, limit: int) -> tuple[str, int, bool]:
    """Decode incrementally, retain only the requested window, preserve CR/LF."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    validator = codecs.getincrementaldecoder("utf-8")("strict")
    text_parts: list[str] = []
    total = 0
    decoding_errors = False
    with path.open("rb") as source:
        while True:
            chunk = source.read(65536)
            final = not chunk
            text = decoder.decode(chunk, final=final)
            if not decoding_errors:
                try:
                    validator.decode(chunk, final=final)
                except UnicodeDecodeError:
                    decoding_errors = True
            left, right = max(0, start - total), min(len(text), start + limit - total)
            if right > left:
                text_parts.append(text[left:right])
            total += len(text)
            if final:
                break
    return "".join(text_parts), total, decoding_errors


class Capture:
    def __init__(self, directory: Path, max_bytes: int):
        self.output_id = uuid4().hex
        self.directory = directory / self.output_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.max_bytes = max_bytes
        self.counts: dict[Stream, int] = {"stdout": 0, "stderr": 0}
        self.files: dict[Stream, BinaryIO] = {}
        try:
            for stream in ("stdout", "stderr"):
                self.files[stream] = (self.directory / f"{stream}.bin").open("xb")
        except OSError:
            self.close()
            raise

    def write(self, stream: Stream, chunk: bytes) -> None:
        available = self.max_bytes - sum(self.counts.values())
        kept = chunk[:available]
        if kept:
            written = self.files[stream].write(kept)
            self.counts[stream] += written
            if written != len(kept):
                raise OSError("short write while saving command output")
        if len(chunk) > available:
            raise CommandError("capture_limit", "combined output exceeded max_capture_bytes")

    def close(self) -> None:
        error = None
        for file in self.files.values():
            try:
                file.close()
            except OSError as exc:
                error = exc
        if error is not None:
            raise error


class OutputStore:
    def __init__(
        self, directory: Path, preview_chars: int, read_max_chars: int, allowed_commands: list[str]
    ):
        self.directory = directory
        self.preview_chars = preview_chars
        self.read_max_chars = read_max_chars
        self.allowed_commands = allowed_commands

    def begin(self, max_bytes: int) -> Capture:
        return Capture(self.directory, max_bytes)

    def finish(self, capture: Capture, result: CommandResult) -> CommandResult:
        capture.close()
        result.output_id = capture.output_id
        for stream in ("stdout", "stderr"):
            path = capture.directory / f"{stream}.bin"
            if path.stat().st_size != capture.counts[stream]:
                raise OSError("captured byte count does not match saved file")
            preview, chars, decoding_errors = scan_text(path, 0, self.preview_chars)
            setattr(result, stream, preview)
            setattr(result, f"{stream}_bytes", capture.counts[stream])
            setattr(result, f"{stream}_truncated", chars > len(preview))
            result.decoding_errors |= decoding_errors
        temporary = capture.directory / "record.tmp"
        with temporary.open("x", encoding="utf-8", newline="\n") as destination:
            destination.write(result.model_dump_json())
            destination.flush()
            os.fsync(destination.fileno())
        temporary.replace(capture.directory / "record.json")
        return result

    def read(self, request: OutputRequest) -> OutputPage:
        # Validate even when invoked internally instead of via MCP.
        request = OutputRequest.model_validate(request.model_dump())
        if request.max_chars > self.read_max_chars:
            raise ValueError(f"max_chars must not exceed {self.read_max_chars}")
        directory = self.directory / request.output_id
        try:
            record = CommandResult.model_validate_json((directory / "record.json").read_bytes())
            if record.output_id != request.output_id:
                raise ValueError("record ID mismatch")
            try:
                if record.command is not None:
                    allowed = resolve_command(record.command, self.allowed_commands) == record.argv
                else:
                    # Older records have argv only; keep reads when those arguments remain allowed.
                    allowed = any(
                        record.argv == command_args(command) for command in self.allowed_commands
                    )
                if not allowed:
                    raise ValueError("saved command is outside the current allowlist")
            except CommandError as exc:
                raise ValueError("saved command is outside the current allowlist") from exc
            for stream in ("stdout", "stderr"):
                if (directory / f"{stream}.bin").stat().st_size != getattr(
                    record, f"{stream}_bytes"
                ):
                    raise ValueError("saved output size mismatch")
            text, total, decoding_errors = scan_text(
                directory / f"{request.stream}.bin", request.start_char, request.max_chars
            )
        except FileNotFoundError as exc:
            raise ValueError("output record not found or not committed") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("output record could not be read") from exc
        if request.start_char > total:
            raise ValueError("start_char exceeds total_chars")
        next_char = request.start_char + len(text)
        return OutputPage(
            output_id=request.output_id,
            stream=request.stream,
            start_char=request.start_char,
            text=text,
            next_char=next_char,
            eof=next_char == total,
            total_chars=total,
            capture_complete=record.capture_complete,
            decoding_errors=record.decoding_errors or decoding_errors,
            error_code=record.error_code,
        )
