#!/usr/bin/env sh
# Zero-install MCP bridge for OnyxSi (macOS / Linux).
# Usage: ./onyxsi-mcp.sh --workspace /path/to/target/repo
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${PYTHON:-python3}" "$here/onyxsi-mcp.py" "$@"
