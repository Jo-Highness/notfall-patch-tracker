# SBOM (Software Bill of Materials)

Für jede Version werden drei SBOMs (mit HTTPS-Overlay vier) im Format **CycloneDX 1.6 (JSON)** erzeugt
(Entscheidung J15 in `docs/DECISIONS.md`):

| Datei (`sbom/`) | Inhalt |
|---|---|
| `notfall-patch-tracker-<version>-source.cdx.json` | Quellbaum: alle Python-Laufzeitabhängigkeiten aus `requirements.lock` und die vendorten Browser-Bibliotheken (Tabulator, Quill inkl. darin gebündelter Pakete) |
| `notfall-patch-tracker-<version>-app.cdx.json` | App-Abbild: Debian-Pakete des Basisabbilds, CPython, installierte Python-Pakete, Dateien mit Prüfsummen |
| `notfall-patch-tracker-<version>-gateway.cdx.json` | Gateway-Abbild: Alpine-Pakete inkl. nginx, Dateien mit Prüfsummen |
| `notfall-patch-tracker-<version>-tls-proxy.cdx.json` | TLS-Proxy-Abbild des HTTPS-Overlays (`public-tls`), nur wenn gebaut (`docker compose -f docker-compose.yml -f docker-compose.tls.yml build public-tls`); sonst übersprungen |

Die Ausgaben stehen in `.gitignore`: Eine SBOM beschreibt ein konkretes Release bzw.
Abbild und gehört als Artefakt dorthin, nicht in den Quellbaum (sie wäre nach jedem
Lock- oder Basisabbild-Update veraltet).

## Erzeugen

Voraussetzung: Docker. Alle Werkzeuge laufen als Wegwerf-Container mit gepinnter Version
(`anchore/syft:v1.56.0`, `cyclonedx/cyclonedx-cli:0.33.1`, für `--scan`
`anchore/grype:v0.121.1`, Nachbearbeitung im Python-Basisabbild des Projekts); auf dem
Host wird nichts installiert.

```bash
# Abbilder bauen (Labels mit Commit, siehe unten), dann SBOMs erzeugen
export NPT_REVISION="$(git rev-parse HEAD)"
docker compose build
scripts/sbom.sh                      # alle SBOMs nach ./sbom, mit Schema-Prüfung
scripts/sbom.sh --scan               # zusätzlich grype-Abgleich -> sbom/*.grype.txt
GRYPE_FAIL_ON=high scripts/sbom.sh --scan   # Exitcode != 0 ab Schweregrad high
SKIP_IMAGES=1 scripts/sbom.sh        # nur Quellbaum, ohne gebaute Abbilder
APP_IMAGE=notfall-patch-tracker:1.2.0 GATEWAY_IMAGE=notfall-patch-tracker-gateway:1.2.0 scripts/sbom.sh
```

Ablauf (`scripts/sbom.sh`):

1. **Quellbaum:** Syft erkennt `requirements.lock` am Dateinamen nicht; das Skript legt
   eine Kopie als `requirements.txt` in ein Staging-Verzeichnis und scannt dieses (die
   Fundstelle wird in der SBOM wieder auf `/requirements.lock` gesetzt). Tests,
   Lasttest-Werkzeuge und `requirements-dev.lock` gehören bewusst nicht dazu – sie
   werden nicht ausgeliefert.
2. **Vendorte JS-Bibliotheken:** Syft erkennt minifizierte Dateien ohne `package.json`
   nicht. Sie stehen deshalb in `scripts/sbom-components.json` und werden von
   `scripts/sbom_finalize.py` als Komponenten mit purl (`pkg:npm/...`), Lizenz,
   SHA-256 je Datei, Fundstelle und SHA-512 des npm-Pakets ergänzt. Das Skript bricht ab,
   wenn Version oder Prüfsummen nicht zur `VERSION`-Datei im Vendor-Verzeichnis passen –
   ein Update der Bibliothek ohne SBOM-Anpassung fällt also auf. In Quill gebündelte
   Pakete (parchment, quill-delta, eventemitter3, lodash-es) sind als Unterkomponenten
   enthalten; ihre Version ist abgeleitet (Property `npt:version-basis`), der deklarierte
   Bereich steht in `npt:version-range`.
3. **Abbilder:** `docker save` → Syft liest das Archiv (`docker-archive:`); der
   Werkzeug-Container bekommt keinen Docker-Socket.
4. **Lizenzen:** Die Requirements-Datei enthält keine Lizenzen; sie werden aus der
   App-Abbild-SBOM (installierte Paket-Metadaten) übernommen. Für Pakete, deren
   Metadaten Syft nicht auswertet, gilt `license_overrides` in
   `scripts/sbom-components.json` (geprüft mit `scripts/check-licenses.sh`). Mit
   `SKIP_IMAGES=1` fehlen daher die Python-Lizenzen in der Quellbaum-SBOM.
5. **Reproduzierbar:** Zeitstempel = `SOURCE_DATE_EPOCH` (Default: Zeit des letzten
   Commits), Komponenten und Abhängigkeiten stabil sortiert, `serialNumber` als UUIDv5
   über den Inhalt. Gleiche Eingaben (Lock, Vendor-Dateien bzw. dasselbe Abbild) ergeben
   byteidentische Dateien. Das gilt für die SBOM eines gegebenen Abbilds; ein Neubau des
   Abbilds selbst ist nicht bitgleich (neue Layer-Digests).
6. **Schema-Prüfung** gegen CycloneDX 1.6 mit `cyclonedx-cli validate`
   (abschaltbar mit `NO_VALIDATE=1`).

## OCI-Labels der Abbilder

`Dockerfile`, `gateway/Dockerfile` und `tls-proxy/Dockerfile` setzen am Ende der Laufzeitstufe:
`org.opencontainers.image.{title,description,licenses=MIT,version,revision,source}`.
`revision` und `source` kommen aus den Build-Args `NPT_REVISION` und `NPT_SOURCE_URL`
(in `docker-compose.yml` aus der Umgebung/.env durchgereicht, Default leer):

```bash
NPT_REVISION="$(git rev-parse HEAD)" NPT_SOURCE_URL=https://github.com/<owner>/notfall-patch-tracker \
  docker compose build
docker image inspect notfall-patch-tracker:latest --format '{{json .Config.Labels}}'
```

## Als Release-Artefakt veröffentlichen

Beim Release (Tag `vX.Y.Z`) die `*.cdx.json` an das Release hängen, z. B. mit der
GitHub-CLI:

```bash
gh release create v0.1.0 --notes-file CHANGELOG-0.1.0.md \
  sbom/notfall-patch-tracker-0.1.0-source.cdx.json \
  sbom/notfall-patch-tracker-0.1.0-app.cdx.json \
  sbom/notfall-patch-tracker-0.1.0-gateway.cdx.json \
  sbom/notfall-patch-tracker-0.1.0-tls-proxy.cdx.json
```

Werden die Abbilder in eine Registry veröffentlicht, sollte die SBOM aus genau dem
veröffentlichten Abbild (gleicher Digest) erzeugt werden – also nach dem Bau, vor dem Push,
mit `APP_IMAGE`/`GATEWAY_IMAGE`/`TLS_PROXY_IMAGE` auf die Release-Tags.

## Optional: SBOM als signierte Attestierung (cosign)

Mit [cosign](https://github.com/sigstore/cosign) lässt sich die SBOM als in-toto-Attestierung
an das Abbild in der Registry binden (Schlüssel aus dem eigenen Secret-Store, nie im Repo):

```bash
IMG=ghcr.io/<owner>/notfall-patch-tracker@sha256:<digest>
cosign attest --yes --key cosign.key --type cyclonedx \
  --predicate sbom/notfall-patch-tracker-0.1.0-app.cdx.json "$IMG"
cosign verify-attestation --key cosign.pub --type cyclonedx "$IMG"
```

Alternativ schlüssellos (Sigstore-OIDC, z. B. in einer CI-Pipeline) ohne `--key`.
Das Beispiel ist im Projekt nicht ausgeführt worden (es gibt noch keine Registry-Abbilder);
gedacht für cosign v3 (Container `ghcr.io/sigstore/cosign/cosign:v3.1.4`), Optionen vor
dem ersten Einsatz mit `cosign attest --help` abgleichen.

## Schwachstellen-Abgleich und Lizenzprüfung

- `scripts/sbom.sh --scan` gleicht alle SBOMs mit grype ab (Datenbank-Cache unter
  `~/.cache/npt-grype`). Befunde der Basisabbilder werden durch Aktualisieren der
  per Digest gepinnten Basisabbilder behoben (`Dockerfile`, `gateway/Dockerfile`,
  `tls-proxy/Dockerfile`); Gateway und TLS-Proxy entfernen zusätzlich ungenutzte
  nginx-Module und curl. Stand und Bewertung der Befunde (inkl. lodash-es in Quill): `docs/DECISIONS.md` K10.
- `scripts/audit-deps.sh` (pip-audit) prüft die Python-Locks direkt gegen PyPI/OSV.
- `scripts/check-licenses.sh` prüft die Lizenzen der Python-Laufzeitabhängigkeiten
  (Befund bei GPL/AGPL oder fehlender Angabe); Übersicht aller Drittlizenzen:
  `THIRD_PARTY_NOTICES.md`.
