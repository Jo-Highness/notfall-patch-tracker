#!/usr/bin/env bash
# Ausrollen des Helm-Charts (NPT-12): lint -> template (inkl. Pflichtwerte und
# DB-Verbindungsbudget) -> optional kubeconform -> helm upgrade --install --wait ->
# Abbild-Abgleich je Pod (laeuft wirklich das gerenderte Abbild, ueberall dasselbe?).
#
#   deploy/helm/deploy.sh -n <namespace> [-r <release>] -f <values.yaml> [-f ...] [--dry-run] [-- <helm-args>]
#
#   -n  Namespace (Pflicht)          -r  Release-Name (Default: npt)
#   -f  Werte-Datei(en)              --dry-run  nur Pruefungen, nichts installieren
#   --  weitere Argumente fuer helm upgrade (z. B. --timeout 15m, --set ...)
#
# Voraussetzungen: helm (3.x oder 4.x), kubectl mit Kontext auf den Ziel-Cluster;
# kubeconform wird genutzt, wenn vorhanden (KUBECONFORM_K8S_VERSION, Default 1.37.0;
# SKIP_KUBECONFORM=1 z. B. ohne Internetzugang fuer die Schemas).
# Exitcode != 0 bei jedem Fehler; der Abbild-Abgleich laeuft auch nach --wait.
set -euo pipefail
ORIG="$PWD"
cd "$(dirname "$0")"
CHART="$PWD/notfall-patch-tracker"

NS=""
REL="npt"
DRY=0
VALUES=()
EXTRA=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n) NS="$2"; shift 2 ;;
    -r) REL="$2"; shift 2 ;;
    -f) VALUES+=(-f "$(cd "$ORIG" && realpath "$2")"); shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --) shift; EXTRA=("$@"); break ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unbekanntes Argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$NS" ]] || { echo "-n <namespace> fehlt" >&2; exit 2; }
[[ ${#VALUES[@]} -gt 0 ]] || { echo "mindestens eine Werte-Datei (-f) angeben" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "== helm lint --strict"
helm lint --strict "$CHART" "${VALUES[@]}" >"$TMP/lint.txt" 2>&1 || { cat "$TMP/lint.txt" >&2; exit 1; }

echo "== helm template (Pflichtwerte, Schema, DB-Verbindungsbudget)"
helm template "$REL" "$CHART" -n "$NS" "${VALUES[@]}" >"$TMP/manifest.yaml" 2>"$TMP/template.err" \
  || { cat "$TMP/template.err" >&2; exit 1; }

if [[ "${SKIP_KUBECONFORM:-0}" != 1 ]] && command -v kubeconform >/dev/null; then
  echo "== kubeconform -strict"
  kubeconform -strict -summary -kubernetes-version "${KUBECONFORM_K8S_VERSION:-1.37.0}" \
    -schema-location default \
    -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
    "$TMP/manifest.yaml"
fi

# Gerenderte Abbilder (ohne helm-test-Pod) als "abbild" je Zeile.
grep -E '^\s+image: ' "$TMP/manifest.yaml" | sed -E 's/^\s+image: "?([^"]+)"?/\1/' | sort -u >"$TMP/images.txt"
echo "== Abbilder laut Manifest"
sed 's/^/   /' "$TMP/images.txt"
if grep -v -q '@sha256:' "$TMP/images.txt"; then
  echo "   Hinweis: nicht alle Abbilder per Digest gepinnt (image.*.digest); Abgleich nur auf Gleichheit je Container."
fi

if [[ "$DRY" == 1 ]]; then
  echo "== --dry-run: keine Installation"
  exit 0
fi

echo "== helm upgrade --install --wait"
helm upgrade --install "$REL" "$CHART" -n "$NS" "${VALUES[@]}" --wait --timeout 10m "${EXTRA[@]}"

echo "== Abbild-Abgleich je Pod"
kubectl -n "$NS" rollout status "deploy/$(kubectl -n "$NS" get deploy -l "app.kubernetes.io/instance=$REL,app.kubernetes.io/component=public" -o jsonpath='{.items[0].metadata.name}')" --timeout=600s >/dev/null
kubectl -n "$NS" rollout status "deploy/$(kubectl -n "$NS" get deploy -l "app.kubernetes.io/instance=$REL,app.kubernetes.io/component=backend" -o jsonpath='{.items[0].metadata.name}')" --timeout=600s >/dev/null
# Terminierende Pods eines vorigen Rollouts abwarten (hoechstens 120 s).
for _ in $(seq 1 40); do
  n="$(kubectl -n "$NS" get pods -l "app.kubernetes.io/instance=$REL,app.kubernetes.io/component in (public,backend)" \
    -o jsonpath='{range .items[*]}{.metadata.deletionTimestamp}{"\n"}{end}' | grep -c . || true)"
  [[ "$n" == 0 ]] && break
  sleep 3
done
kubectl -n "$NS" get pods -l "app.kubernetes.io/instance=$REL,app.kubernetes.io/component in (public,backend)" \
  -o jsonpath='{range .items[*]}{range .status.containerStatuses[*]}{.name}{"\t"}{.imageID}{"\t"}{end}{"|"}{range .spec.containers[*]}{.name}{"\t"}{.image}{"\t"}{end}{"|"}{.metadata.name}{"\n"}{end}' \
  >"$TMP/pods.txt"
fail=0
declare -A seen
while IFS='|' read -r status spec pod; do
  [[ -n "$pod" ]] || continue
  read -r -a st <<<"$status"
  read -r -a sp <<<"$spec"
  declare -A want=()
  for ((i = 0; i < ${#sp[@]}; i += 2)); do want["${sp[i]}"]="${sp[i+1]}"; done
  for ((i = 0; i < ${#st[@]}; i += 2)); do
    c="${st[i]}"; id="${st[i+1]}"; img="${want[$c]:-}"
    if ! grep -qxF "$img" "$TMP/images.txt"; then
      echo "   FEHLER $pod/$c: Abbild $img nicht im Manifest (alter Rollout?)"; fail=1
    fi
    if [[ "$img" == *@sha256:* && "$id" != *"${img##*@}" ]]; then
      echo "   FEHLER $pod/$c: laeuft $id, erwartet ${img##*@}"; fail=1
    fi
    key="$c $img"
    if [[ -n "${seen[$key]:-}" && "${seen[$key]}" != "$id" ]]; then
      echo "   FEHLER $pod/$c: anderes Abbild als andere Pods (${seen[$key]} != $id)"; fail=1
    fi
    seen[$key]="$id"
    echo "   ok  $pod/$c  ${id##*/}"
  done
  unset want
done <"$TMP/pods.txt"
[[ "$fail" == 0 ]] || { echo "== Abbild-Abgleich FEHLGESCHLAGEN" >&2; exit 1; }
echo "== Abbild-Abgleich ok"
echo "== helm test"
helm test "$REL" -n "$NS" --logs
