"""Run the production CLI/FastMCP against an executable ADB simulator."""

from fake_process import install_fake_process

from adb_mcp.__main__ import main

install_fake_process()
main()
