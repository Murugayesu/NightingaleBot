#!/usr/bin/env sh
# Evaluation gate: fails (exit 1) when retrieval or answer quality drops below
# the thresholds. Runs inside the compose stack, so it uses the real corpus,
# models and configuration. Results are saved and visible in the admin panel.
#
#   MIN_HIT_RATE=0.8 MIN_PASS_RATE=0.7 sh scripts/eval-gate.sh
set -eu

MIN_HIT_RATE="${MIN_HIT_RATE:-0.80}"
MIN_PASS_RATE="${MIN_PASS_RATE:-0.70}"
run() { docker compose run --rm --no-deps migrate python -m nursing_bot.cli "$@"; }

echo "== Retrieval (no LLM cost), gate: hit_rate >= $MIN_HIT_RATE"
run eval retrieval --save --min-hit-rate "$MIN_HIT_RATE"

echo "== Answers (one LLM call per case), gate: pass_rate >= $MIN_PASS_RATE"
run eval answer --save --min-pass-rate "$MIN_PASS_RATE"

echo "Eval gate passed."
