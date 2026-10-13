"""Map only the simulator executable to a real Python subprocess."""

import asyncio
import sys
from pathlib import Path

SIMULATOR = Path(__file__).with_name("fake_adb.py")


def install_fake_process() -> None:
    original = asyncio.create_subprocess_exec

    async def create(program, *args, **kwargs):
        if program == "fake-adb":
            return await original(sys.executable, str(SIMULATOR), *args, **kwargs)
        return await original(program, *args, **kwargs)

    asyncio.create_subprocess_exec = create
