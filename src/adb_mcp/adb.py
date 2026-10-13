"""Exact-target ADB subprocess lifecycle. No local shell and no query replay."""

import asyncio
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime

import anyio

from .commands import CommandError, CommandResult, Stream, shell_command, validate_command
from .config import Config, NetworkTarget
from .outputs import Capture, OutputStore

logger = logging.getLogger(__name__)


class ADBService:
    def __init__(self, config: Config):
        self.config = config
        self.store = OutputStore(
            config.output_directory, config.preview_max_chars_per_stream, config.read_max_chars
        )
        self._lock = asyncio.Lock()
        self._cleanup_failed = False

    async def _cleanup(self, process: asyncio.subprocess.Process, work: asyncio.Task) -> None:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            except OSError as exc:
                self._cleanup_failed = True
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
                raise CommandError("cleanup_failed", "could not terminate ADB process") from exc
        try:
            await asyncio.wait_for(
                asyncio.shield(work), timeout=self.config.cleanup_timeout_seconds
            )
        except TimeoutError as exc:
            self._cleanup_failed = True
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            raise CommandError("cleanup_failed", "ADB process/pipe cleanup did not finish") from exc
        except (CommandError, OSError):
            # Reader errors are reported by the execution path; process wait still completed.
            pass
        if process.returncode is None:
            self._cleanup_failed = True
            raise CommandError("cleanup_failed", "ADB process exit was not confirmed")

    async def _execute(
        self, args: list[str], write: Callable[[Stream, bytes], None], timeout_seconds: float
    ) -> int:
        # Protect process creation so cancellation cannot lose the process handle.
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                self.config.adb_path,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        )
        spawn_cancellation: asyncio.CancelledError | None = None
        try:
            process = await asyncio.shield(spawning)
        except asyncio.CancelledError as exc:
            spawn_cancellation = exc
            with anyio.CancelScope(shield=True):
                while not spawning.done():
                    try:
                        await asyncio.shield(spawning)
                    except asyncio.CancelledError:
                        continue
                try:
                    process = spawning.result()
                except OSError:
                    raise spawn_cancellation from None
        except OSError as exc:
            raise CommandError(
                "adb_unavailable", "could not start configured ADB executable"
            ) from exc

        failure = asyncio.get_running_loop().create_future()

        async def pump(stream: Stream, reader: asyncio.StreamReader) -> None:
            try:
                while True:
                    try:
                        chunk = await reader.read(65536)
                    except OSError as exc:
                        raise CommandError("capture_failed", "ADB pipe read failed") from exc
                    if not chunk:
                        break
                    try:
                        write(stream, chunk)
                    except OSError as exc:
                        raise CommandError("storage_failed", "output stream write failed") from exc
            except CommandError as exc:
                if not failure.done():
                    failure.set_result(exc)
                raise

        async def collect() -> int:
            # Always wait for both pipes and the process, including on reader errors.
            results = await asyncio.gather(
                pump("stdout", process.stdout),
                pump("stderr", process.stderr),
                process.wait(),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            return process.returncode

        # A reader error needs to stop the process immediately, not wait for EOF.
        work = asyncio.create_task(collect())
        try:
            if spawn_cancellation is not None:
                raise spawn_cancellation
            done, _ = await asyncio.wait(
                [work, failure], timeout=timeout_seconds, return_when=asyncio.FIRST_COMPLETED
            )
            if failure in done:
                raise failure.result()
            if work not in done:
                raise TimeoutError
            return work.result()
        except BaseException:
            # This task survives cancellation of its caller while killing/reaping ADB.
            with anyio.CancelScope(shield=True):
                cleanup = asyncio.create_task(self._cleanup(process, work))
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        continue
                cleanup.result()
            raise
        finally:
            failure.cancel()

    async def _control(self, args: list[str], timeout_seconds: float) -> tuple[int, str, str]:
        buffers: dict[Stream, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}

        def write(stream: Stream, chunk: bytes) -> None:
            if sum(map(len, buffers.values())) + len(chunk) > 65536:
                raise CommandError("adb_error", "ADB connection response exceeded 64 KiB")
            buffers[stream].extend(chunk)

        code = await self._execute(args, write, timeout_seconds)
        return (
            code,
            buffers["stdout"].decode("utf-8", "replace"),
            buffers["stderr"].decode("utf-8", "replace"),
        )

    async def _prepare(self) -> None:
        deadline = time.monotonic() + self.config.connect_timeout_seconds

        async def control(args: list[str]) -> tuple[int, str, str]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            return await self._control(args, remaining)

        async def states() -> list[str]:
            code, stdout, _ = await control(["devices", "-l"])
            if code != 0:
                raise CommandError("adb_error", "adb devices failed")
            return [
                fields[1]
                for line in stdout.splitlines()
                if len(fields := line.split()) >= 2 and fields[0] == self.config.target_serial
            ]

        found = await states()
        if not found and isinstance(self.config.target, NetworkTarget) and self.config.auto_connect:
            code, _, _ = await control(["connect", self.config.target_serial])
            if code != 0:
                raise CommandError("connect_failed", "adb connect failed for configured target")
            found = await states()
        if not found:
            raise CommandError("device_not_found", "configured target is absent from adb devices")
        if len(found) != 1:
            raise CommandError("device_ambiguous", "configured target has multiple device entries")
        state = found[0]
        if state != "device":
            code = {"offline": "device_offline", "unauthorized": "device_unauthorized"}.get(
                state, "device_not_ready"
            )
            raise CommandError(code, f"configured target state: {state}")
        code, stdout, _ = await control(["-s", self.config.target_serial, "features"])
        if code != 0:
            raise CommandError("adb_error", "could not query exact-target ADB features")
        if "shell_v2" not in re.split(r"[,\s]+", stdout.strip()):
            raise CommandError(
                "shell_protocol_unsupported",
                "shell_v2 is required for separate streams/exit status",
            )

    async def run(self, argv: list[str]) -> CommandResult:
        # Reject before any process submission; serialize all work for the fixed target.
        argv = validate_command(argv)
        async with self._lock:
            started = datetime.now(UTC).isoformat()
            clock = time.monotonic()
            result = CommandResult(
                target_serial=self.config.target_serial,
                argv=argv,
                started_at=started,
                finished_at=started,
                duration_ms=0,
            )
            capture: Capture | None = None
            cancellation: asyncio.CancelledError | None = None
            preparing = True
            try:
                if self._cleanup_failed:
                    raise CommandError(
                        "cleanup_failed", "previous cleanup failed; restart required"
                    )
                capture = self.store.begin(self.config.max_capture_bytes)
                await self._prepare()
                preparing = False
                result.exit_code = await self._execute(
                    ["-s", self.config.target_serial, "shell", "-T", "-n", shell_command(argv)],
                    capture.write,
                    self.config.command_timeout_seconds,
                )
                result.capture_complete = True
                if result.exit_code != 0:
                    result.error_code = "command_failed"
                    result.error_message = "ADB shell exited with a nonzero status"
            except CommandError as exc:
                result.error_code, result.error_message = exc.code, str(exc)
            except TimeoutError:
                result.error_code = "connect_timeout" if preparing else "command_timeout"
                result.error_message = (
                    "ADB preparation timed out" if preparing else "query timed out"
                )
            except asyncio.CancelledError as exc:
                cancellation = exc
                result.error_code, result.error_message = "cancelled", "query cancelled by caller"
            except OSError:
                result.error_code, result.error_message = "storage_failed", "output storage failed"
            result.finished_at = datetime.now(UTC).isoformat()
            result.duration_ms = round((time.monotonic() - clock) * 1000)
            if capture is not None:
                try:
                    if result.error_code == "storage_failed":
                        # Do not publish a readable ID after an evidence write failure.
                        capture.close()
                        result.stdout_bytes = capture.counts["stdout"]
                        result.stderr_bytes = capture.counts["stderr"]
                    else:
                        result = self.store.finish(capture, result)
                except OSError:
                    try:
                        capture.close()
                    except OSError:
                        pass
                    result.output_id = None
                    result.capture_complete = False
                    result.error_code, result.error_message = (
                        "storage_failed",
                        "record commit failed",
                    )
            if cancellation is not None:
                logger.warning(
                    "cancelled output_id=%s target=%s", result.output_id, result.target_serial
                )
                raise cancellation
            return result
