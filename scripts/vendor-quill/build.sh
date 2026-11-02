#!/usr/bin/env bash
# Baut das Quill-Browser-Bundle unter src/npt/static/vendor/quill reproduzierbar neu
# (DECISIONS K14): quill 2.0.3 aus der npm-Registry, aber mit lodash-es 4.18.1 statt der
# beim Upstream-Build gebuendelten 4.17.21. Alle Versionen stehen exakt in package.json und
# mit Registry-Integrity in package-lock.json; Node und esbuild sind gepinnt. Laeuft in
# einem Wegwerf-Container, auf dem Host wird nichts installiert, node_modules entsteht nur
# im Container.
#
#   scripts/vendor-quill/build.sh          Bundle bauen, Dateien + VERSION schreiben
#   CHECK=1 scripts/vendor-quill/build.sh  nur bauen und mit den Dateien im Baum
#                                          vergleichen (Exitcode 1 bei Abweichung)
#   LOCK=1 scripts/vendor-quill/build.sh   package-lock.json neu erzeugen (nach einer
#                                          Versionsaenderung in package.json), dann bauen
#
# Ausgabe: quill.min.js (esbuild, IIFE, window.Quill), quill.snow.css (unveraendert aus
# dem npm-Paket, ohne sourceMappingURL), LICENSE (Quill), quill.js.LICENSE.txt
# (Lizenztexte der gebuendelten Pakete), VERSION (Herkunft und Pruefsummen; wird von
# scripts/sbom_finalize.py gegen scripts/sbom-components.json geprueft).
set -euo pipefail
cd "$(dirname "$0")/../.."
NODE_IMAGE="${NODE_IMAGE:-node:22.23.4-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914}"
DEST="src/npt/static/vendor/quill"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
chmod 0777 "$OUT"

docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e npm_config_cache=/tmp/.npm \
  -e npm_config_update_notifier=false -e npm_config_fund=false -e npm_config_audit=false \
  -e LOCK="${LOCK:-0}" -e NODE_IMAGE="$NODE_IMAGE" \
  -v "$PWD/scripts/vendor-quill:/src" -v "$OUT:/out" -w /tmp "$NODE_IMAGE" sh -eu -c '
mkdir -p /tmp/b && cd /tmp/b
cp /src/package.json /src/entry.js .
if [ "$LOCK" = "1" ]; then
  npm install --package-lock-only --ignore-scripts --no-audit
  cp package-lock.json /src/package-lock.json
else
  cp /src/package-lock.json .
fi
# Installation nur aus dem Lockfile (Integrity-Pruefung durch npm), ohne Install-Skripte.
npm ci --ignore-scripts --no-audit
node_modules/.bin/esbuild entry.js --bundle --format=iife --target=es2020 --minify \
  --legal-comments=eof --charset=utf8 --log-level=warning --outfile=/out/quill.min.js
sed "/^\/\*# sourceMappingURL=.*\*\/$/d" node_modules/quill/dist/quill.snow.css > /out/quill.snow.css
cp node_modules/quill/LICENSE /out/LICENSE
# Lizenztexte aller gebuendelten Pakete (Pflicht bei MIT/BSD: Hinweis mitliefern).
for p in quill parchment quill-delta eventemitter3 fast-diff lodash.clonedeep lodash.isequal lodash-es; do
  v="$(node -p "require(\"./node_modules/$p/package.json\").version")"
  printf "==== %s %s ====\n" "$p" "$v"
  f="$(ls node_modules/$p | grep -iE "^licen[cs]e" | head -n1)"
  if [ -n "$f" ]; then cat "node_modules/$p/$f"; else echo "(keine Lizenzdatei im Paket; Lizenz laut package.json: $(node -p "require(\"./node_modules/$p/package.json\").license"))"; fi
  echo
done > /out/quill.js.LICENSE.txt
# Herkunft: Versionen und Registry-Integrity aus dem Lockfile.
node -e "
const lock = require(\"./package-lock.json\").packages;
const pick = [\"quill\", \"parchment\", \"quill-delta\", \"eventemitter3\", \"fast-diff\",
  \"lodash.clonedeep\", \"lodash.isequal\", \"lodash-es\", \"esbuild\"];
for (const n of pick) { const p = lock[\"node_modules/\" + n]; console.log(n, p.version, p.integrity); }
// lodash-es darf nur einmal (in der Zielversion) vorkommen.
const dup = Object.keys(lock).filter((k) => k.endsWith(\"node_modules/lodash-es\"));
if (dup.length !== 1) { console.error(\"lodash-es mehrfach im Lockfile: \" + dup); process.exit(1); }
" > /out/lock.txt
node --version > /out/node.txt
'

# VERSION-Datei aus den Lockfile-Angaben und Pruefsummen.
ver() { awk -v n="$1" '$1 == n {print $2}' "$OUT/lock.txt"; }
integ() { awk -v n="$1" '$1 == n {print $3}' "$OUT/lock.txt"; }
sha() { sha256sum "$1" | cut -d' ' -f1; }
{
  echo "quill $(ver quill) (BSD-3-Clause, siehe LICENSE), neu gebuendelt mit lodash-es $(ver lodash-es)"
  echo "Gebuendelt (Lizenztexte in quill.js.LICENSE.txt): parchment $(ver parchment) (BSD-3-Clause),"
  echo "  fast-diff $(ver fast-diff) (Apache-2.0), quill-delta $(ver quill-delta), eventemitter3 $(ver eventemitter3),"
  echo "  lodash.clonedeep $(ver lodash.clonedeep), lodash.isequal $(ver lodash.isequal), lodash-es $(ver lodash-es) (MIT)"
  echo "Quelle: npm registry, quill-$(ver quill).tgz"
  echo "  sha512 (Registry-Integrity, geprueft): $(integ quill | sed 's/^sha512-//')"
  echo "Bau: scripts/vendor-quill/build.sh (esbuild $(ver esbuild), Node $(cat "$OUT/node.txt"), ${NODE_IMAGE%%@*})"
  echo "  Eingaben exakt per scripts/vendor-quill/package-lock.json (npm ci, Integrity je Paket)"
  echo "  quill.min.js: esbuild-Bundle aus quill/quill.js (ESM des npm-Pakets) als window.Quill"
  echo "  quill.snow.css: dist/quill.snow.css unveraendert (ohne sourceMappingURL-Kommentar)"
  echo "  sha256 quill.min.js:   $(sha "$OUT/quill.min.js")"
  echo "  sha256 quill.snow.css: $(sha "$OUT/quill.snow.css")"
} > "$OUT/VERSION"

FILES=(quill.min.js quill.snow.css LICENSE quill.js.LICENSE.txt VERSION)
if [[ "${CHECK:-0}" == "1" ]]; then
  rc=0
  for f in "${FILES[@]}"; do
    if cmp -s "$OUT/$f" "$DEST/$f"; then echo "gleich:    $DEST/$f"; else echo "ABWEICHUNG: $DEST/$f"; rc=1; fi
  done
  exit $rc
fi
for f in "${FILES[@]}"; do cp "$OUT/$f" "$DEST/$f"; done
cat "$DEST/VERSION"
