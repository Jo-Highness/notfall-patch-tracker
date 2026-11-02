#!/usr/bin/env bash
# Vollstaendiger Lasttest gegen einen laufenden Stack (Methodik: docs/LOADTEST.md).
#
#   loadtest/run-loadtest.sh /p/<token> [ausgabeverzeichnis]
#
# Umgebung (Defaults in Klammern):
#   LT_HOST (http://127.0.0.1:8480)  LT_USERS (500)  LT_SPAWN (25/s)  LT_TIME (5m)
#   LT_PROCESSES (2)                  Locust-Prozesse
#   LT_CPUSET ("")                    CPUs fuer Locust, z. B. "4,5" (App auf andere CPUs pinnen)
#   LT_STATS_MATCH (npt-dev)          Container-Namensmuster fuer CPU/RAM-Messung
#   LT_PROBE (1)                      Browser-Probe (E2E-Image) waehrend der Messung
#   LT_PROBE_CPUSET (= LT_CPUSET)
#   LT_LIVE_PROBE (1)                 unabhaengige Live-Latenz-Probe (loadtest/live_probe.py)
# Ergebnis: <aus>/lt_stats.csv (Locust), <aus>/docker-stats.csv, <aus>/browser.json,
#           <aus>/live_probe.json, <aus>/summary.json (loadtest/summarize.py)
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT_PATH="${1:?Projektpfad /p/<token> angeben}"
OUT="${2:-/tmp/npt-loadtest-$(date +%Y%m%d-%H%M%S)}"
HOST="${LT_HOST:-http://127.0.0.1:8480}"
USERS="${LT_USERS:-500}"
SPAWN="${LT_SPAWN:-25}"
DURATION="${LT_TIME:-5m}"
PROCS="${LT_PROCESSES:-2}"
CPUSET="${LT_CPUSET:-}"
MATCH="${LT_STATS_MATCH:-npt-dev}"
mkdir -p "$OUT"
chmod 777 "$OUT"
cpu_args=()
[[ -n "$CPUSET" ]] && cpu_args=(--cpuset-cpus "$CPUSET")

docker build -q -t npt-loadtest loadtest >/dev/null

# CPU/RAM aller Container alle 5 s (App, DB, Gateway, Locust, Nachbarn)
( while :; do
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' \
      | sed "s/^/$(date +%s),/" >> "$OUT/docker-stats.csv"
    sleep 5
  done ) &
STATS_PID=$!
trap 'kill $STATS_PID 2>/dev/null || true' EXIT

ramp_s=$(( (USERS + SPAWN - 1) / SPAWN ))
if [[ "${LT_PROBE:-1}" == "1" ]]; then
  # Browser-Probe nach Hochlauf + 60 s, solange die Last laeuft
  ( sleep $(( ramp_s + 60 ))
    pcpu=()
    [[ -n "${LT_PROBE_CPUSET:-$CPUSET}" ]] && pcpu=(--cpuset-cpus "${LT_PROBE_CPUSET:-$CPUSET}")
    docker run --rm "${pcpu[@]}" --network host -v "$PWD:/w:ro" -w /w npt-e2e \
      python loadtest/browser_probe.py --url "$HOST$PROJECT_PATH/" --runs 10 \
      > "$OUT/browser.json" 2> "$OUT/browser.err" || true ) &
fi

if [[ "${LT_LIVE_PROBE:-1}" == "1" ]]; then
  # Unabhaengige Live-Latenz-Probe (stdlib, kaum CPU) nach Hochlauf + 30 s fuer 120 s
  ( sleep $(( ramp_s + 30 ))
    tset=()
    [[ -n "$CPUSET" ]] && command -v taskset >/dev/null && tset=(taskset -c "$CPUSET")
    "${tset[@]}" python3 loadtest/live_probe.py --host "$HOST" --project "$PROJECT_PATH" \
      --duration "${LT_PROBE_DURATION:-120}" --interval 0.5 > "$OUT/live_probe.json" 2> "$OUT/live_probe.err" || true ) &
fi

echo "Locust: $USERS Nutzer, $SPAWN/s, $DURATION nach Hochlauf (${ramp_s} s), $PROCS Prozesse ${CPUSET:+(CPUs $CPUSET)}"
docker run --rm "${cpu_args[@]}" --network host --name npt-locust \
  -e NPT_PROJECT_PATH="$PROJECT_PATH" \
  -v "$PWD/loadtest:/lt:ro" -v "$OUT:/out" \
  npt-loadtest -f /lt/locustfile.py --headless --processes "$PROCS" \
  -u "$USERS" -r "$SPAWN" -t "$DURATION" --reset-stats \
  --host "$HOST" --csv /out/lt --only-summary 2>&1 | tail -60 || true  # Exitcode 1 bei Fehlern -> trotzdem auswerten
wait_probe() { for _ in $(seq 1 60); do [[ -s "$OUT/browser.json" ]] && return; sleep 2; done; }
[[ "${LT_PROBE:-1}" == "1" ]] && wait_probe
kill $STATS_PID 2>/dev/null || true
python3 loadtest/summarize.py "$OUT" "$MATCH" | tee "$OUT/summary.json"
