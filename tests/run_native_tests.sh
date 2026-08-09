#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TEST_BINARY=$(mktemp /tmp/state-notifier-sn2.XXXXXX)
trap 'rm -f "$TEST_BINARY"' EXIT INT TERM

/usr/bin/clang++ -std=c++17 -Wall -Wextra -Werror \
  -I"$PROJECT_DIR/main" \
  "$PROJECT_DIR/main/sn2_protocol.cpp" \
  "$PROJECT_DIR/tests/firmware_logic_test.cpp" \
  -o "$TEST_BINARY"
"$TEST_BINARY"
echo "firmware SN2 tests: OK"
