"""Stand-in for the smart-gallery CLI used by the job runner tests.

Usage: fake_cli.py progress N | echo TEXT | fail | wait | hang
Like the real CLI, it logs to stderr in loguru's format (``fail`` logs its
message at ERROR) and ends with a ``Details: <path>`` log line.
"""

import os
import sys
import time
from pathlib import Path

from smart_gallery.progress import ProgressEvent

LIMIT = 60  # seconds; keeps a forgotten child from outliving the test run


def log(level: str, message: str) -> None:
    """A line as loguru writes it, colors included."""
    print(
        f"[32m2026-10-17 20:11:25.507[0m | [1m{level:<8}[0m | "
        f"__main__:main:854 - {message}",
        file=sys.stderr,
        flush=True,
    )


def main(args: list[str]) -> int:
    code = 0
    command = args[0] if args else ""
    if command == "progress":
        n = int(args[1])
        for i in range(1, n + 1):
            print(ProgressEvent("Copying files", i, n, (("copied", i),)).to_line())
    elif command == "echo":
        print(" ".join(args[1:]))
    elif command == "fail":
        log("ERROR", "something broke")
        code = 1
    elif command == "wait":
        stop = Path(os.environ["SG_CANCEL_FILE"])
        print("waiting", flush=True)
        deadline = time.monotonic() + LIMIT
        while not stop.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        code = 130
    elif command == "hang":
        print("hanging", flush=True)
        time.sleep(LIMIT)
    log("INFO", f"Details: {Path('logs') / (command + '.log')}")
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
