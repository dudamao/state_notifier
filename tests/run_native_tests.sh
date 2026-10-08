#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TEST_BINARY=$(mktemp "${TMPDIR:-/tmp}/state-notifier-sn2.XXXXXX")
trap 'rm -f "$TEST_BINARY" "$TEST_BINARY.exe"' EXIT INT TERM

# Prefer clang++ where present (macOS), fall back to g++ (Windows/MinGW).
if [ -z "${CXX:-}" ]; then
    if command -v clang++ >/dev/null 2>&1; then
        CXX=clang++
    else
        CXX=g++
    fi
fi

"$CXX" -std=c++17 -Wall -Wextra -Werror \
  -I"$PROJECT_DIR/main" \
  "$PROJECT_DIR/main/sn2_protocol.cpp" \
  "$PROJECT_DIR/main/font5x7.cpp" \
  "$PROJECT_DIR/main/text_render.cpp" \
  "$PROJECT_DIR/main/clock_text.cpp" \
  "$PROJECT_DIR/tests/firmware_logic_test.cpp" \
  -o "$TEST_BINARY"
"$TEST_BINARY"
echo "firmware SN2 tests: OK"

# The C glyph table must stay byte-identical to the host renderer's table.
if [ -z "${PYTHON:-}" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON=python3
    else
        PYTHON=python
    fi
fi
"$PYTHON" "$PROJECT_DIR/tests/check_font_parity.py"
