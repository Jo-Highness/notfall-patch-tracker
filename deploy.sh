#!/usr/bin/env bash
# Baut und startet den Stack und beweist, dass jeder Dienst das frisch gebaute
# Abbild verwendet und gesund ist. `docker compose up -d` tauscht Container nach
# einem Rebuild nicht immer aus – abweichende Dienste werden gezielt neu erstellt.
#   ./deploy.sh            (nutzt .env im Repo-Verzeichnis)
set -euo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo ".env fehlt (cp .env.example .env und ausfuellen)" >&2; exit 1; }
SERVICES=(app admin-gateway)
# HTTPS-Overlay (docker-compose.tls.yml): public-tls wird ebenfalls gebaut und geprueft.
if docker compose config --services | grep -x public-tls >/dev/null; then SERVICES+=(public-tls); fi

docker compose build --pull
docker compose up -d --remove-orphans

image_matches() { # dienst -> 0, wenn der laufende Container das aktuelle Abbild des Tags nutzt
  local cid running wanted
  cid="$(docker compose ps -q "$1")"
  [[ -n "$cid" ]] || return 1
  running="$(docker inspect --format '{{.Image}}' "$cid")"
  wanted="$(docker image inspect --format '{{.Id}}' "$(docker inspect --format '{{.Config.Image}}' "$cid")")"
  [[ "$running" == "$wanted" ]]
}

for svc in "${SERVICES[@]}"; do
  if ! image_matches "$svc"; then
    echo "$svc nutzt nicht das aktuelle Abbild -> neu erstellen"
    docker compose up -d --no-deps --force-recreate "$svc"
  fi
done

echo "Warte auf gesunde Container ..."
for _ in $(seq 1 40); do
  states="$(docker compose ps --format '{{.Service}}={{.Health}}')"
  if ! grep -qv '=healthy' <<<"$states"; then break; fi
  sleep 3
done
docker compose ps

rc=0
for svc in "${SERVICES[@]}"; do
  cid="$(docker compose ps -q "$svc")"
  health="$(docker inspect --format '{{.State.Health.Status}}' "$cid")"
  short="$(docker inspect --format '{{.Image}}' "$cid" | cut -c8-19)"
  if image_matches "$svc" && [[ "$health" == "healthy" ]]; then
    echo "OK     $svc laeuft mit aktuellem Abbild ($short), $health"
  else
    echo "FEHLER $svc: Abbild $short, Zustand $health" >&2
    rc=1
  fi
done
exit $rc
