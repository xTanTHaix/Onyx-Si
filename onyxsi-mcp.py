#!/usr/bin/env python3
"""Zero-install MCP bridge for OnyxSi.

Runs the OnyxSi stdio JSON-RPC server straight from this checkout:
no ``pip install``, no ``PYTHONPATH``, no virtualenv, no build step.
Point your MCP client at this file and pass the repository you want it to
work on:

    python /path/to/OnyxSi/onyxsi-mcp.py --workspace /path/to/target/repo

The bridge only:
  1. puts ``<this checkout>/src`` first on ``sys.path``;
  2. pins stdin/stdout/stderr to UTF-8 so non-ASCII paths survive a
     cp1252 / cp874 console;
  3. disables ``.pyc`` writing so running it never dirties the checkout;
  4. hands control to ``onyxsi.server:main`` unchanged.

It writes nothing to stdout itself: stdout belongs to the JSON-RPC
protocol. Diagnostics go to stderr.
"""

from __future__ import annotations

import sys
from pathlib import Path

MIN_PYTHON = (3, 11)


def _fail(message: str, code: int = 2) -> int:
    sys.stderr.write(f"onyxsi-mcp: {message}\n")
    sys.stderr.flush()
    return code


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        have = ".".join(str(part) for part in sys.version_info[:3])
        want = ".".join(str(part) for part in MIN_PYTHON)
        return _fail(f"Python {want}+ is required, found {have}")

    root = Path(__file__).resolve().parent
    src = root / "src"
    package = src / "onyxsi" / "server.py"
    if not package.is_file():
        return _fail(f"cannot find src/onyxsi/server.py under {root}; is this a full checkout?")

    # Keep the checkout pristine: importing from src must not create __pycache__.
    sys.dont_write_bytecode = True

    # MCP is UTF-8 JSON over pipes; do not inherit a legacy console code page.
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    sys.path.insert(0, str(src))

    try:
        from onyxsi.server import main as server_main
    except Exception as exc:  # noqa: BLE001 - surface import failures on stderr, not stdout
        return _fail(f"failed to import onyxsi.server: {exc}")

    try:
        return server_main()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
