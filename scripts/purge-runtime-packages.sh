#!/bin/sh
# Laeuft beim Bau der Laufzeitstufe (Dockerfile, DECISIONS K14): entfernt ungenutzte
# Debian-Pakete und bricht ab, wenn das Ergebnis nicht dem gepflegten Stand entspricht.
#   1. Jedes zu entfernende Paket muss installiert sein. dpkg --purge uebergeht fehlende
#      Pakete still; nach einem neuen Basis-Digest (Paket umbenannt, z. B. libapt-pkg7.0 ->
#      libapt-pkg8.0) bliebe das neue Paket sonst unbemerkt im Abbild.
#   2. Entfernen in zwei dpkg-Aufrufen ($NPT_PURGE_PACKAGES, dann $NPT_PURGE_PACKAGES_LATE):
#      dpkg waehlt die Reihenfolge innerhalb eines Aufrufs selbst, die PAM-Skripte brauchen
#      debconf und dpkg selbst braucht tar.
#   3. Die verbleibenden Pakete muessen genau der Liste <erwartet.txt> entsprechen
#      (neue Pakete im Basisabbild sind zu bewerten: entfernen oder in die Liste aufnehmen).
# Aufruf: purge-runtime-packages.sh <erwartet.txt>; Exitcode 1 bei Abweichung.
set -eu
expected="$1"
: "${NPT_PURGE_PACKAGES:?}" "${NPT_PURGE_PACKAGES_LATE:?}"
hint="Paketlisten (Dockerfile: NPT_PURGE_PACKAGES, NPT_PURGE_PACKAGES_LATE; scripts/runtime-packages.txt) an das Basisabbild anpassen, DECISIONS K14."

missing=""
for p in $NPT_PURGE_PACKAGES $NPT_PURGE_PACKAGES_LATE; do
  status="$(dpkg-query -W -f='${db:Status-Status}' "$p" 2>/dev/null || true)"
  [ "$status" = installed ] || missing="$missing $p"
done
if [ -n "$missing" ]; then
  echo "purge-runtime-packages: zu entfernende Pakete sind nicht installiert (umbenannt oder entfallen?):$missing" >&2
  echo "purge-runtime-packages: $hint" >&2
  exit 1
fi

# shellcheck disable=SC2086 # Wortaufteilung der Paketlisten ist gewollt
dpkg --purge --force-remove-essential --force-remove-protected --force-depends $NPT_PURGE_PACKAGES
# shellcheck disable=SC2086
dpkg --purge --force-remove-essential --force-depends $NPT_PURGE_PACKAGES_LATE

tmp="$(mktemp -d)"
dpkg-query -W -f='${db:Status-Status} ${Package}\n' | awk '$1 != "not-installed" { print $2 }' \
  | sort -u > "$tmp/ist"
grep -Ev '^[[:space:]]*(#|$)' "$expected" | sed 's/[[:space:]]*$//' | sort -u > "$tmp/soll"
extra="$(comm -23 "$tmp/ist" "$tmp/soll" | tr '\n' ' ')"
gone="$(comm -13 "$tmp/ist" "$tmp/soll" | tr '\n' ' ')"
count="$(wc -l < "$tmp/ist")"
rm -rf "$tmp"
if [ -n "$extra$gone" ]; then
  [ -z "$extra" ] || echo "purge-runtime-packages: im Abbild, aber nicht erwartet: $extra" >&2
  [ -z "$gone" ] || echo "purge-runtime-packages: erwartet, aber nicht im Abbild: $gone" >&2
  echo "purge-runtime-packages: $hint" >&2
  exit 1
fi
echo "purge-runtime-packages: Paketstand wie erwartet ($count Pakete)"
