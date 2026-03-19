#!/usr/bin/env bash
set -euo pipefail

seconds="${1:-30}"
out="logs/profile/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$out"

llama_id="$(docker compose -f compose.yaml ps -q llama-server)"
ws_id="$(docker compose -f compose.yaml ps -q ws-api)"
pids=()

cleanup() {
  for pid in "${pids[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
}

trap cleanup EXIT

nvidia-smi dmon -s pucmt -d 1 -o DT -f "$out/gpu.csv" &
pids+=("$!")

(
  while :; do
    printf "=== %s ===\n" "$(date -Is)"
    docker stats --no-stream --format '{{json .}}' "$llama_id" "$ws_id"
    printf "\n"
    sleep 1
  done
) > "$out/docker-stats.jsonl" &
pids+=("$!")

(
  while :; do
    printf '{"ts":"%s","slots":' "$(date -Is)"
    curl -sf http://127.0.0.1:8081/slots || printf 'null'
    printf '}\n'
    sleep 1
  done
) > "$out/slots.jsonl" &
pids+=("$!")

(
  while :; do
    printf "# ts=%s\n" "$(date -Is)"
    curl -sf http://127.0.0.1:8081/metrics || true
    printf "\n"
    sleep 5
  done
) > "$out/metrics.prom" &
pids+=("$!")

sleep "$seconds"
cleanup
trap - EXIT

docker compose -f compose.yaml logs --since "${seconds}s" llama-server > "$out/llama-server.log"
echo "$out"
