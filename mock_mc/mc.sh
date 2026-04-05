#!/usr/bin/env bash
# mc.sh  –  Shell entry point for the mock memory compiler.
#
# Usage:
#   ./mc.sh  <memory_name>  [<outdir>]          keyword / positional mode
#   ./mc.sh  -file config.txt  [-DATASHEET]     config-file mode (TSMC Perl compat)
#
# All arguments are forwarded to compile.py verbatim.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "${SCRIPT_DIR}/compile.py" "$@"
