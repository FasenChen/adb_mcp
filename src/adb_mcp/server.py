"""Two compact FastMCP tools with typed structured output and one JSON text block."""

import asyncio
from datetime import UTC, datetime
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field, StrictStr

from .adb import ADBService
from .commands import CommandError, CommandResult, OutputPage, OutputRequest, Stream
from .config import Config


def tool_result(model: BaseModel, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=model.model_dump_json())],
        structuredContent=model.model_dump(mode="json"),
        isError=is_error,
    )


def create_server(config: Config) -> FastMCP:
    service = ADBService(config)
    server = FastMCP("adb-query", log_level="WARNING")
    annotations = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    )

    @server.tool(
        annotations=annotations,
        description="Run a fixed-target read-only query: getprop [property], settings get/list "
        "global/system/secure, or dumpsys display (no extra flags). No shell operators. "
        "Returns raw stream previews, exit status and persistent output_id; no case verdict.",
    )
    async def run_adb_shell(
        argv: Annotated[list[StrictStr], Field(min_length=1, max_length=32, strict=True)],
    ) -> Annotated[CallToolResult, CommandResult]:
        try:
            result = await service.run(argv)
        except CommandError as exc:
            now = datetime.now(UTC).isoformat()
            result = CommandResult(
                target_serial=config.target_serial,
                argv=argv,
                started_at=now,
                finished_at=now,
                duration_ms=0,
                error_code=exc.code,
                error_message=str(exc),
            )
        return tool_result(result, is_error=result.error_code is not None)

    @server.tool(
        annotations=annotations,
        description="Read saved stdout/stderr without executing ADB. Offsets count Unicode "
        f"code points; preserve newlines. max_chars <= {config.read_max_chars}. "
        "Includes capture completeness and decoding status.",
    )
    async def read_command_output(
        output_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$", strict=True)],
        stream: Stream = "stdout",
        start_char: Annotated[int, Field(ge=0, strict=True)] = 0,
        max_chars: Annotated[int, Field(ge=1, le=config.read_max_chars, strict=True)] = min(
            6000, config.read_max_chars
        ),
    ) -> Annotated[CallToolResult, OutputPage]:
        try:
            request = OutputRequest(
                output_id=output_id, stream=stream, start_char=start_char, max_chars=max_chars
            )
            return tool_result(await asyncio.to_thread(service.store.read, request))
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    return server
