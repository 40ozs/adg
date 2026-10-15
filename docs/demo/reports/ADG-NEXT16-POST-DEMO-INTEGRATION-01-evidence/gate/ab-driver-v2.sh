#!/usr/bin/env bash
# Interleaved A/B driver for gate v2 (ADG-NEXT16-POST-DEMO-INTEGRATION-01). Each round runs one
# gate run per (build, slow-origin condition), in the same order, each in a fresh browser
# process. Every run is appended to its own JSONL file; nothing is retried.
#
#   ab-driver-v2.sh <rounds> <out dir>
# Proxies: :3200 N15 rate, :3210 N16 rate (8 KiB every 20 ms); :3300 N15 chunk, :3310 N16 chunk
# (40 ms per upstream chunk). N15 = main 32da1ae (Next 15.5.25), N16 = integration (Next 16.4.1).
set -u
ROUNDS=$1
OUT=$2
HERE=$(cd "$(dirname "$0")" && pwd)
export NODE_PATH=$(npm root -g)
mkdir -p "$OUT"
for i in $(seq 1 "$ROUNDS"); do
  for cfg in "n15-rate http://localhost:3200" "n16-rate http://localhost:3210" \
             "n15-chunk40 http://localhost:3300" "n16-chunk40 http://localhost:3310"; do
    set -- $cfg
    node "$HERE/react418-gate-v2.script.js" "$2" "$1" 1 "$OUT/$1.jsonl" 2>&1 | sed "s/run 1:/round $i:/"
  done
done
echo "ab-driver-v2 done"
