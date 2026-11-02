#!/usr/bin/env bash
# Erzeugt eine Test-PKI fuer Entwicklung und Tests (Auftrag 8.1). NUR fuer Tests/CI:
# die Ausgaben stehen in .gitignore und werden nie in ein Deployment gemountet (NPT-11).
#
#   scripts/gen-test-pki.sh [ausgabeverzeichnis]      (Default: ./pki-out)
#   FORCE=1 scripts/gen-test-pki.sh                    vorhandene Ausgabe ersetzen
#   GATEWAY_SANS="DNS:npt.example.org,IP:192.0.2.10"   zusaetzliche Server-SANs
#
# Ergebnis:
#   <out>/gateway/{ca-bundle.pem,server.crt,server.key,crl.pem}   -> ADMIN_PKI_DIR
#   <out>/clients/{admin,nonadmin,foreign,revoked}.{crt,key}      -> curl --cert/--key
#   <out>/clients/*.p12 (Passwort: $P12_PASSWORD, Default "npt-test") fuer Browser-Import
#   <out>/admin-bootstrap-id.txt                                  -> ADMIN_BOOTSTRAP_IDS
set -euo pipefail

OUT="${1:-./pki-out}"
DAYS="${DAYS:-825}"
P12_PASSWORD="${P12_PASSWORD:-npt-test}"
EXTRA_SANS="${GATEWAY_SANS:-}"

if [[ -e "$OUT/gateway/ca-bundle.pem" && "${FORCE:-0}" != "1" ]]; then
  echo "Test-PKI existiert bereits in $OUT (FORCE=1 zum Ersetzen)." >&2
  exit 0
fi
rm -rf "$OUT"
mkdir -p "$OUT"/{ca,foreign-ca,gateway,clients}
umask 077
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

newkey() { openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$1" 2>/dev/null; }

make_ca() { # dir name cn
  local dir="$1" name="$2" cn="$3"
  newkey "$dir/$name.key"
  openssl req -x509 -new -key "$dir/$name.key" -sha256 -days "$DAYS" \
    -subj "/C=DE/O=NPT Test/OU=Test PKI/CN=$cn" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" \
    -out "$dir/$name.crt"
}

# openssl-ca-Datenbank fuer die CRL der Test-CA
mkdir -p "$WORK/ca-db"
: > "$WORK/ca-db/index.txt"
echo 1000 > "$WORK/ca-db/crlnumber"
cat > "$WORK/ca.cnf" <<EOF
[ ca ]
default_ca = test_ca
[ test_ca ]
database = $WORK/ca-db/index.txt
crlnumber = $WORK/ca-db/crlnumber
certificate = $OUT/ca/test-ca.crt
private_key = $OUT/ca/test-ca.key
default_md = sha256
default_crl_days = 30
EOF

sign() { # ca_dir ca_name key out subj ext_file
  local ca_dir="$1" ca_name="$2" key="$3" out="$4" subj="$5" ext="$6"
  openssl req -new -key "$key" -subj "$subj" -out "$WORK/req.csr"
  openssl x509 -req -in "$WORK/req.csr" -CA "$ca_dir/$ca_name.crt" -CAkey "$ca_dir/$ca_name.key" \
    -CAcreateserial -CAserial "$WORK/$ca_name.srl" -sha256 -days "$DAYS" \
    -extfile "$ext" -out "$out" 2>/dev/null
}

client_ext() { # file san
  cat > "$1" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=clientAuth
subjectAltName=$2
EOF
}

echo "Test-CA und fremde CA ..."
make_ca "$OUT/ca" test-ca "NPT Test Admin CA"
make_ca "$OUT/foreign-ca" foreign-ca "Fremde Test CA"

echo "Server-Zertifikat (Gateway) ..."
newkey "$OUT/gateway/server.key"
SAN="DNS:localhost,IP:127.0.0.1,DNS:admin-gateway"
[[ -n "$EXTRA_SANS" ]] && SAN="$SAN,$EXTRA_SANS"
cat > "$WORK/server.ext" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
subjectAltName=$SAN
EOF
sign "$OUT/ca" test-ca "$OUT/gateway/server.key" "$OUT/gateway/server.crt" \
  "/C=DE/O=NPT Test/CN=localhost" "$WORK/server.ext"

echo "Client-Zertifikate ..."
# admin: DN + SAN-E-Mail + UPN (fuer alle ADMIN_CERT_ID_ATTRIBUTE-Varianten nutzbar)
client_ext "$WORK/admin.ext" "email:admin@npt.test,otherName:1.3.6.1.4.1.311.20.2.3;UTF8:admin@npt.test"
newkey "$OUT/clients/admin.key"
sign "$OUT/ca" test-ca "$OUT/clients/admin.key" "$OUT/clients/admin.crt" \
  "/C=DE/O=NPT Test/OU=Admins/CN=Test Admin" "$WORK/admin.ext"

client_ext "$WORK/nonadmin.ext" "email:bearbeiter@npt.test"
newkey "$OUT/clients/nonadmin.key"
sign "$OUT/ca" test-ca "$OUT/clients/nonadmin.key" "$OUT/clients/nonadmin.crt" \
  "/C=DE/O=NPT Test/OU=Fachbereich/CN=Test Nichtadmin" "$WORK/nonadmin.ext"

client_ext "$WORK/revoked.ext" "email:gesperrt@npt.test"
newkey "$OUT/clients/revoked.key"
sign "$OUT/ca" test-ca "$OUT/clients/revoked.key" "$OUT/clients/revoked.crt" \
  "/C=DE/O=NPT Test/OU=Admins/CN=Test Gesperrt" "$WORK/revoked.ext"

# fremde CA: gleicher Subject-DN wie der Admin (darf trotzdem nicht durchkommen)
client_ext "$WORK/foreign.ext" "email:admin@npt.test"
newkey "$OUT/clients/foreign.key"
sign "$OUT/foreign-ca" foreign-ca "$OUT/clients/foreign.key" "$OUT/clients/foreign.crt" \
  "/C=DE/O=NPT Test/OU=Admins/CN=Test Admin" "$WORK/foreign.ext"

echo "CRL (revoked.crt gesperrt) ..."
openssl ca -config "$WORK/ca.cnf" -revoke "$OUT/clients/revoked.crt" -crl_reason keyCompromise 2>/dev/null
openssl ca -config "$WORK/ca.cnf" -gencrl -out "$OUT/gateway/crl.pem" 2>/dev/null

cp "$OUT/ca/test-ca.crt" "$OUT/gateway/ca-bundle.pem"
cp "$OUT/ca/test-ca.crt" "$OUT/clients/ca.crt"

for c in admin nonadmin revoked foreign; do
  openssl pkcs12 -export -inkey "$OUT/clients/$c.key" -in "$OUT/clients/$c.crt" \
    -name "NPT Test $c" -passout "pass:$P12_PASSWORD" -out "$OUT/clients/$c.p12"
done

# Lesbar fuer den Gateway-Container (nginx-Master liest als root; Test-PKI only).
chmod 0755 "$OUT" "$OUT/gateway" "$OUT/clients"
chmod 0644 "$OUT"/gateway/* "$OUT"/clients/*

openssl x509 -in "$OUT/clients/admin.crt" -noout -subject -nameopt RFC2253 \
  | sed 's/^subject=//' > "$OUT/admin-bootstrap-id.txt"
echo "Fertig. Admin-Identifier (subject_dn): $(cat "$OUT/admin-bootstrap-id.txt")"
