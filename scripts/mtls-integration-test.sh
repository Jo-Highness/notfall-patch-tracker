#!/usr/bin/env bash
# mTLS-Integrationstest gegen den laufenden Compose-Stack (Auftrag 10, NPT-06).
#
# Voraussetzungen: Stack laeuft (docker compose up -d), Test-PKI aus scripts/gen-test-pki.sh,
# ADMIN_BOOTSTRAP_IDS enthaelt den Admin-Identifier aus <pki>/admin-bootstrap-id.txt,
# ADMIN_CRL_PATH zeigt auf die CRL der Test-PKI (fuer den Sperr-Test).
#
#   GATEWAY_URL=https://localhost:8481 PUBLIC_URL=http://127.0.0.1:8480 \
#   COMPOSE_PROJECT_NAME=npt-dev scripts/mtls-integration-test.sh [pki-verzeichnis]
#
# Der Test legt eine Freischaltungsanfrage fuer das Nicht-Admin-Zertifikat an,
# bestaetigt sie, prueft den Zugang und entfernt den Admin-Eintrag wieder.
set -uo pipefail

PKI="${1:-./pki-out}"
GATEWAY_URL="${GATEWAY_URL:-https://localhost:8443}"
PUBLIC_URL="${PUBLIC_URL:-http://127.0.0.1:8080}"
PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"
APP_ADMIN_IP="${APP_ADMIN_IP:-172.31.250.10}"
CURL_IMAGE="${CURL_IMAGE:-curlimages/curl:8.17.0}"
CA="$PKI/clients/ca.crt"
C="$PKI/clients"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pass=0; fail=0
ok()   { echo "  OK    $1"; pass=$((pass + 1)); }
bad()  { echo "  FEHLER $1"; fail=$((fail + 1)); }
check() { # beschreibung erwartet ist
  if [[ "$3" == "$2" ]]; then ok "$1 ($3)"; else bad "$1: erwartet $2, erhalten $3"; fi
}

# gw <cert|-> <methode> <pfad> [curl-args...] -> schreibt Body nach $WORK/body, gibt Status aus
# (curl -w liefert bei Verbindungsfehlern selbst "000").
gw() {
  local who="$1" method="$2" path="$3"; shift 3
  local auth=()
  [[ "$who" != "-" ]] && auth=(--cert "$C/$who.crt" --key "$C/$who.key")
  curl -sS -o "$WORK/body" -w '%{http_code}' --cacert "$CA" "${auth[@]}" -X "$method" \
    "$@" "$GATEWAY_URL$path" 2>"$WORK/err" || :
}

csrf_from_body() { sed -n 's/.*<meta name="csrf-token" content="\([^"]*\)".*/\1/p' "$WORK/body" | head -1; }

echo "mTLS-Integrationstest gegen $GATEWAY_URL (Projekt $PROJECT)"

echo "1. Ohne Client-Zertifikat"
check "kein Zertifikat -> abgewiesen" 403 "$(gw - GET /admin/)"

echo "2. Zertifikat einer fremden CA"
code="$(gw foreign GET /admin/)"
if [[ "$code" == "403" || "$code" == "000" ]]; then ok "fremde CA abgewiesen ($code)"; else bad "fremde CA: $code"; fi
if grep -q "Admin-Bereich" "$WORK/body" 2>/dev/null; then bad "fremde CA sieht Admin-Inhalt"; fi

echo "3. Gesperrtes Zertifikat (CRL)"
if [[ -f "$C/revoked.crt" ]]; then
  code="$(gw revoked GET /admin/)"
  if [[ "$code" == "403" || "$code" == "000" ]]; then ok "gesperrtes Zertifikat abgewiesen ($code)"; else bad "gesperrt: $code"; fi
fi

echo "4. Gueltiges Nicht-Admin-Zertifikat -> Anfrage-Seite"
check "Nicht-Admin /admin/ -> 403" 403 "$(gw nonadmin GET /admin/)"
grep -q "Freischaltung anfragen\|ist offen" "$WORK/body" && ok "Anfrage-Seite angezeigt" || bad "Anfrage-Seite fehlt"
grep -q "test nichtadmin" "$WORK/body" && ok "Identifier angezeigt" || bad "Identifier fehlt"

echo "5. Admin-Zertifikat"
check "Admin /admin/ -> 200" 200 "$(gw admin GET /admin/)"
grep -q "Admin-Bereich" "$WORK/body" && ok "Admin-Seite" || bad "Admin-Seite fehlt"
check "Admin /admin -> 308" 308 "$(gw admin GET /admin)"
check "Gateway leitet nur /admin weiter (/healthz -> 404)" 404 "$(gw admin GET /healthz)"
check "Gateway /adminx -> 404" 404 "$(gw admin GET /adminx)"

echo "6. Gespoofte Header ueber das Gateway (werden ueberschrieben)"
spoof=(-H "X-SSL-Client-Verify: SUCCESS" -H "X-SSL-Client-S-DN: CN=Test Admin,OU=Admins,O=NPT Test,C=DE"
       -H "X-SSL-Client-Fingerprint: 0000" -H "X-Forwarded-For: 172.31.250.11")
check "Nicht-Admin mit gefaelschten Headern bleibt Nicht-Admin" 403 "$(gw nonadmin GET /admin/ "${spoof[@]}")"

echo "7. CSRF"
gw admin GET /admin/benutzer >/dev/null
token="$(csrf_from_body)"
[[ -n "$token" ]] && ok "CSRF-Token im Meta-Tag" || bad "kein CSRF-Token"
check "POST ohne Token -> 403" 403 "$(gw admin POST /admin/benutzer/neu -H "Origin: $GATEWAY_URL" --data-urlencode 'identifier=CN=x')"
check "POST mit fremdem Origin -> 403" 403 "$(gw admin POST /admin/benutzer/neu -H 'Origin: https://evil.example' --data-urlencode "csrf_token=$token" --data-urlencode 'identifier=CN=x')"

echo "8. Freischaltung: Anfrage -> Bestaetigung -> Zugang -> Entfernen"
gw nonadmin GET /admin/zugang >/dev/null
ntoken="$(csrf_from_body)"
check "Anfrage stellen -> 303" 303 "$(gw nonadmin POST /admin/zugang/anfrage -H "Origin: $GATEWAY_URL" --data-urlencode "csrf_token=$ntoken")"
gw admin GET /admin/benutzer >/dev/null
rid="$(grep -o '/admin/anfragen/[0-9]*/bestaetigen' "$WORK/body" | head -1 | grep -o '[0-9]*')"
if [[ -n "$rid" ]]; then
  ok "offene Anfrage sichtbar (#$rid)"
  check "Bestaetigen -> 303" 303 "$(gw admin POST "/admin/anfragen/$rid/bestaetigen" -H "Origin: $GATEWAY_URL" --data-urlencode "csrf_token=$token")"
  check "Nicht-Admin hat jetzt Zugang" 200 "$(gw nonadmin GET /admin/)"
  gw admin GET /admin/benutzer >/dev/null
  # Liste ist nach aktiv/Anlagezeit sortiert: der eben freigeschaltete Eintrag steht zuletzt.
  aid="$(sed -n 's|.*action="/admin/benutzer/\([0-9]*\)/entfernen".*|\1|p' "$WORK/body" | tail -1)"
  check "Admin-Eintrag wieder entfernen -> 303" 303 "$(gw admin POST "/admin/benutzer/$aid/entfernen" -H "Origin: $GATEWAY_URL" --data-urlencode "csrf_token=$token")"
  check "Nicht-Admin wieder ohne Zugang" 403 "$(gw nonadmin GET /admin/)"
else
  bad "offene Anfrage nicht gefunden"
fi

echo "9. Gespoofte Header direkt an den App-Admin-Port aus anderen Containern"
admin_net="${PROJECT}_admin_net"
public_net="${PROJECT}_public_net"
cert_hdr="$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(open(sys.argv[1]).read(),safe=""))' "$C/admin.crt" 2>/dev/null || true)"
fp="$(openssl x509 -in "$C/admin.crt" -noout -fingerprint -sha1 | cut -d= -f2 | tr -d : | tr 'A-F' 'a-f')"
direct() { # netz
  docker run --rm --network "$1" "$CURL_IMAGE" -s -o /dev/null -w '%{http_code}' --max-time 5 \
    -H 'X-SSL-Client-Verify: SUCCESS' -H "X-SSL-Client-Fingerprint: $fp" \
    -H "X-SSL-Client-Cert: $cert_hdr" -H "X-Forwarded-For: 172.31.250.11" \
    "http://$APP_ADMIN_IP:8001/admin/" 2>/dev/null || :
}
check "aus admin_net (fremde IP) -> 403" 403 "$(direct "$admin_net")"
code="$(direct "$public_net")"
if [[ "$code" == "000" || "$code" == "403" ]]; then ok "aus public_net nicht erreichbar/abgewiesen ($code)"; else bad "public_net: $code"; fi

echo "10. Bearbeiter-Port"
pub() { curl -sS -o "$WORK/body" -w '%{http_code}' "$@" 2>/dev/null || :; }
check "/healthz -> 200" 200 "$(pub "$PUBLIC_URL/healthz")"
check "/readyz -> 200" 200 "$(pub "$PUBLIC_URL/readyz")"
check "/robots.txt -> 200" 200 "$(pub "$PUBLIC_URL/robots.txt")"
for p in /admin /admin/ /admin/benutzer /admin/static/css/app.css; do
  check "$p auf Bearbeiter-Port -> 404" 404 "$(pub "$PUBLIC_URL$p")"
done
check "/admin/ mit gefaelschten Headern -> 404" 404 \
  "$(pub -H 'X-SSL-Client-Verify: SUCCESS' -H "X-SSL-Client-Fingerprint: $fp" -H "X-SSL-Client-Cert: $cert_hdr" "$PUBLIC_URL/admin/")"
hdrs="$(curl -sS -D - -o /dev/null "$PUBLIC_URL/" 2>/dev/null | tr -d '\r')"
grep -qi '^content-security-policy:.*script-src .self.' <<<"$hdrs" && ! grep -qi 'unsafe-inline' <<<"$hdrs" \
  && ok "CSP ohne unsafe-inline" || bad "CSP"
grep -qi '^referrer-policy: no-referrer' <<<"$hdrs" && ok "Referrer-Policy no-referrer" || bad "Referrer-Policy"

echo
echo "Ergebnis: $pass bestanden, $fail fehlgeschlagen"
[[ $fail -eq 0 ]]
