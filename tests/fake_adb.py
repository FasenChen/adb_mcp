"""Executable simulator: real pipes, exit codes, delays, and exact argv logging."""

import json
import os
import shlex
import sys
import time
from pathlib import Path

args = sys.argv[1:]
serial = os.environ.get("FAKE_ADB_SERIAL", "test-device")
mode = os.environ.get("FAKE_ADB_MODE", "normal")
log_path = os.environ.get("FAKE_ADB_LOG")
if log_path:
    with Path(log_path).open("a", encoding="utf-8") as log:
        log.write(json.dumps(args) + "\n")

if args == ["devices", "-l"]:
    print("List of devices attached")
    if mode == "connect_slow":
        time.sleep(60)
    elif mode == "absent":
        print("other-device\tdevice product:other")
    elif mode in {"offline", "unauthorized"}:
        print(f"{serial}\t{mode}")
    elif mode == "ambiguous":
        print(f"{serial}\tdevice\n{serial}\tdevice")
    elif mode in {"connect", "connect_fail"}:
        if Path(os.environ["FAKE_ADB_CONNECTED"]).exists():
            print(f"{serial}\tdevice product:simulated")
    else:
        print(f"{serial}\tdevice product:simulated")
elif args == ["connect", serial]:
    if mode == "connect_fail":
        print("cannot connect", file=sys.stderr)
        sys.exit(1)
    Path(os.environ["FAKE_ADB_CONNECTED"]).touch()
    print(f"connected to {serial}")
elif args == ["-s", serial, "features"]:
    print("cmd" if mode == "old_shell" else "cmd\nshell_v2\nstat_v2")
elif args[:5] == ["-s", serial, "shell", "-T", "-n"] and len(args) == 6:
    if mode == "disconnect_during_query":
        sys.stderr.buffer.write(b"adb: configured device disconnected\n")
        sys.exit(1)
    argv = shlex.split(args[5])
    key = argv[-1]
    query_mode = json.loads(os.environ.get("FAKE_ADB_QUERY_MODES", "{}")).get(
        key, os.environ.get("FAKE_ADB_QUERY_MODE", "normal")
    )
    if query_mode == "empty":
        pass
    elif query_mode == "fail":
        sys.stdout.buffer.write(b"partial\r\n")
        sys.stderr.buffer.write(b"remote error\n")
        sys.exit(7)
    elif query_mode == "slow":
        sys.stdout.buffer.write(b"before timeout\r\n")
        sys.stdout.buffer.flush()
        time.sleep(60)
    elif query_mode == "long":
        for _ in range(20):
            sys.stdout.buffer.write(("中😀e\u0301\r\n" * 1000).encode())
            sys.stdout.buffer.flush()
            sys.stderr.buffer.write(b"E" * 10000)
            sys.stderr.buffer.flush()
    elif query_mode == "single_line":
        sys.stdout.buffer.write(("中😀" * 100000).encode())
    elif query_mode == "limit":
        sys.stdout.buffer.write(b"L" * 20000)
        sys.stdout.buffer.flush()
        time.sleep(60)
    elif query_mode == "invalid_utf8":
        sys.stdout.buffer.write(b"hello\xff\xfe\r\n")
    elif query_mode == "literal_replacement":
        sys.stdout.buffer.write("valid \ufffd\n".encode())
    elif query_mode == "exact_limit":
        sys.stdout.buffer.write(b"X" * 128)
    elif query_mode == "warning":
        sys.stdout.buffer.write((key + "\r\n").encode())
        sys.stderr.buffer.write(b"query warning\n")
    else:
        sys.stdout.buffer.write((key + "\r\n").encode())
else:
    print("unexpected simulated ADB arguments", file=sys.stderr)
    sys.exit(99)
