"""Two FastMCP tools returning raw text and only necessary output notices."""

import asyncio
import json
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from .adb import ADBService
from .commands import (
    CommandError,
    CommandResult,
    OutputPage,
    OutputRequest,
    Stream,
)
from .config import Config


def tool_result(result: CommandResult | OutputPage) -> CallToolResult:
    notices = []
    if isinstance(result, CommandResult):
        content = [TextContent(type="text", text=result.stdout)]
        if result.stderr:
            content.append(TextContent(type="text", text="[stderr]\n" + result.stderr))
        if result.error_code:
            notices.append(
                f"error={result.error_code}; exit_code={result.exit_code}; {result.error_message}"
            )
        for stream in ("stdout", "stderr"):
            if getattr(result, f"{stream}_truncated"):
                notices.append(f"{stream}_truncated=true")
                if result.output_id:
                    cursor = {
                        "output_id": result.output_id,
                        "stream": stream,
                        "start_char": len(getattr(result, stream)),
                    }
                    notices.append(
                        "read_command_output " + json.dumps(cursor, separators=(",", ":"))
                    )
    else:
        content = [TextContent(type="text", text=result.text)]
        if result.error_code:
            notices.append(f"source_error={result.error_code}")
        if not result.eof:
            cursor = {
                "output_id": result.output_id,
                "stream": result.stream,
                "start_char": result.next_char,
            }
            notices.append("read_command_output " + json.dumps(cursor, separators=(",", ":")))
    if not result.capture_complete:
        notices.append("capture_complete=false; saved output is incomplete")
    if result.decoding_errors:
        notices.append("decoding_errors=true; invalid UTF-8 was replaced")
    if notices:
        content.append(TextContent(type="text", text="[adb_mcp]\n" + "\n".join(notices)))
    return CallToolResult(
        content=content,
        isError=isinstance(result, CommandResult) and result.error_code is not None,
    )


def create_server(config: Config) -> FastMCP:
    service = ADBService(config)
    server = FastMCP("adb-query", log_level="WARNING")
    annotations = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    )

    @server.tool(
        annotations=ToolAnnotations(openWorldHint=True),
        description="Run a command that exactly matches allowed_commands in the config, "
        "including the 'adb shell ' prefix, case and whitespace, and return raw stdout. "
        "The device is fixed by the config. "
        "Errors, stderr and incomplete/truncated output are reported separately; "
        "continuation includes read_command_output arguments.",
    )
    async def run_adb_shell(
        command: str,
    ) -> CallToolResult:
        try:
            result = await service.run(command)
        except CommandError as exc:
            raise ToolError(str(exc)) from exc
        return tool_result(result)

    @server.tool(
        annotations=annotations,
        description="Read saved stdout/stderr without executing ADB. Offsets count Unicode "
        f"code points; preserve newlines. max_chars <= {config.read_max_chars}. "
        "Returns raw text; continuation and source errors are reported separately.",
    )
    async def read_command_output(
        output_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$", strict=True)],
        stream: Stream = "stdout",
        start_char: Annotated[int, Field(ge=0, strict=True)] = 0,
        max_chars: Annotated[int, Field(ge=1, le=config.read_max_chars, strict=True)] = min(
            6000, config.read_max_chars
        ),
    ) -> CallToolResult:
        try:
            request = OutputRequest(
                output_id=output_id, stream=stream, start_char=start_char, max_chars=max_chars
            )
            return tool_result(await asyncio.to_thread(service.store.read, request))
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    return server
