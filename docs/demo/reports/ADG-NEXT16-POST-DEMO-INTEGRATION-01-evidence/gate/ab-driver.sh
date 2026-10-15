#!/usr/bin/env bash
# Interleaved A/B driver for the React #418 gate (ADG-NEXT16-POST-DEMO-INTEGRATION-01).
# Each round runs one gate run per (build, condition), in the same order, each in a fresh
# browser process. Every run is appended to its own JSONL file; nothing is retried.
#
#   ab-driver.sh <rounds> <out dir>
# Servers: :3001 N15 direct, :3000 N16 direct, :3011 N15 via +100 ms, :3010 N16 via +100 ms.
set -u
ROUNDS=$1
OUT=$2
HERE=$(cd "$(dirname "$0")" && pwd)
export NODE_PATH=$(npm root -g)
mkdir -p "$OUT"
for i in $(seq 1 "$ROUNDS"); do
  for cfg in "n15-direct http://localhost:3001" "n16-direct http://localhost:3000" \
             "n15-l100 http://localhost:3011" "n16-l100 http://localhost:3010"; do
    set -- $cfg
    node "$HERE/react418-gate.script.js" "$2" "$1" 1 "$OUT/$1.jsonl" 2>&1 | sed "s/run 1:/round $i:/"
  done
done
echo "ab-driver done"
