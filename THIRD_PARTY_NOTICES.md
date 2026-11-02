# Hinweise zu Drittkomponenten

Der Notfall-Patch-Tracker selbst steht unter der [MIT-Lizenz](LICENSE). Er enthält bzw.
nutzt die folgenden Komponenten Dritter unter deren eigenen Lizenzen. Maschinenlesbar
(inkl. Prüfsummen) stehen alle Komponenten in der SBOM ([docs/SBOM.md](docs/SBOM.md)).
Die Lizenzprüfung der Python-Laufzeitabhängigkeiten ist automatisiert:
`scripts/check-licenses.sh` (schlägt bei GPL/AGPL oder fehlender Lizenzangabe fehl).

Stand: Version 0.2.1, `requirements.lock` und `src/npt/static/vendor` dieses Commits.

## 1. Im Repository mitgelieferte Browser-Bibliotheken (`src/npt/static/vendor`)

Tabulator unverändert aus dem npm-Registry-Paket übernommen (nur der
`sourceMappingURL`-Kommentar entfernt). Quill ist aus den Quellen des npm-Pakets
quill 2.0.3 neu gebündelt (`scripts/vendor-quill/build.sh`, mit lodash-es 4.18.1 statt
4.17.21; `quill.snow.css` unverändert). Herkunft und Prüfsummen in der jeweiligen
`VERSION`-Datei, Lizenztext in der jeweiligen `LICENSE`-Datei daneben; die Lizenztexte aller
in Quill gebündelten Pakete stehen in `quill.js.LICENSE.txt`.

| Komponente | Version | Lizenz | Lizenztext |
|---|---|---|---|
| [Tabulator](https://tabulator.info) (`tabulator-tables`) | 6.7.1 | MIT | `src/npt/static/vendor/tabulator/LICENSE` |
| [Quill](https://quilljs.com) | 2.0.3 | BSD-3-Clause | `src/npt/static/vendor/quill/LICENSE` |
| ↳ gebündelt in Quill: parchment | 3.0.0 | BSD-3-Clause | `src/npt/static/vendor/quill/quill.js.LICENSE.txt` |
| ↳ gebündelt in Quill: quill-delta | 5.1.0 | MIT | ebd. |
| ↳ gebündelt in Quill: eventemitter3 | 5.0.1 | MIT | ebd. |
| ↳ gebündelt in Quill: fast-diff | 1.3.0 | Apache-2.0 | ebd. |
| ↳ gebündelt in Quill: lodash.clonedeep | 4.5.0 | MIT | ebd. |
| ↳ gebündelt in Quill: lodash.isequal | 4.5.0 | MIT | ebd. |
| ↳ gebündelt in Quill: lodash-es | 4.18.1 | MIT | ebd. |

Die Versionen der in Quill gebündelten Pakete sind exakt die aus
`scripts/vendor-quill/package-lock.json` (npm-Registry, Integrity je Paket geprüft).
Nur zum Bau (nicht ausgeliefert): esbuild 0.28.2 (MIT) in `node:22.23.4-alpine`.

## 2. Python-Laufzeitabhängigkeiten (`requirements.lock`)

Werden beim Bau des App-Abbilds aus PyPI installiert (Hash-gepinnt), nicht im Repository
mitgeliefert. Lizenz laut Paket-Metadaten (`License-Expression` bzw. Klassifikator),
ermittelt mit `scripts/check-licenses.sh`.

| Paket | Version | Lizenz |
|---|---|---|
| alembic | 1.20.1 | MIT |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| cffi | 2.1.1 | MIT-0 |
| click | 8.5.0 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| et-xmlfile | 2.0.0 | MIT |
| fastapi | 0.162.0 | MIT |
| gunicorn | 26.2.0 | MIT |
| h11 | 0.16.0 | MIT |
| httptools | 0.9.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| jinja2 | 3.1.6 | BSD-3-Clause |
| mako | 1.4.3 | MIT |
| markdown-it-py | 4.2.0 | MIT |
| markupsafe | 3.0.4 | BSD-3-Clause |
| mdurl | 0.1.2 | MIT |
| nh3 | 0.3.7 | MIT |
| openpyxl | 3.1.5 | MIT |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| **psycopg** | 3.3.6 | **LGPL-3.0-only** |
| **psycopg-binary** | 3.3.6 | **LGPL-3.0-only** (+ gebündelte native Bibliotheken, s. u.) |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.1 | MIT |
| pydantic-core | 2.50.1 | MIT |
| pydantic-settings | 2.16.1 | MIT |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| pyyaml | 6.0.3 | MIT |
| sqlalchemy | 2.1.6 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| uvicorn-worker | 0.4.0 | BSD-3-Clause |
| uvloop | 0.23.0 | MIT OR Apache-2.0 (enthält libuv, MIT) |
| watchfiles | 1.3.0 | MIT |
| websockets | 17.3 | BSD-3-Clause |

Einige Wheels enthalten kompilierten Code mit statisch eingebundenen Bibliotheken unter
verträglichen Lizenzen (z. B. cryptography: OpenSSL, Apache-2.0; httptools: llhttp, MIT;
pydantic-core, nh3, watchfiles: Rust-Crates unter MIT/Apache-2.0).

### psycopg / psycopg-binary (LGPL-3.0) und die gebündelte libpq

psycopg steht unter der GNU Lesser General Public License v3. Der Notfall-Patch-Tracker
importiert psycopg nur als eigenständiges, unverändertes Python-Paket (über SQLAlchemy);
es wird weder verändert noch in den Code des Projekts übernommen. Das ist mit der
MIT-Lizenz des Projekts vereinbar. Wer das App-Abbild weitergibt, gibt damit auch psycopg
weiter und muss die LGPL-Pflichten erfüllen: Lizenztext beilegen (liegt im Abbild unter
`/opt/venv/lib/python3.12/site-packages/psycopg*-3.3.6.dist-info/`), auf den Quellcode
verweisen (<https://github.com/psycopg/psycopg>, Tag `3.3.6`) und den Austausch der
Bibliothek ermöglichen (gegeben: `requirements.lock` ändern und Abbild neu bauen).

Das Wheel `psycopg-binary` bringt eine eigene libpq und deren Abhängigkeiten als native
Bibliotheken mit (`psycopg_binary.libs/`):

| Bibliothek | Projekt | Lizenz |
|---|---|---|
| libpq | PostgreSQL | PostgreSQL |
| libssl, libcrypto | OpenSSL 3 | Apache-2.0 |
| libgssapi_krb5, libkrb5, libk5crypto, libkrb5support, libcom_err | MIT Kerberos / e2fsprogs | MIT |
| libldap, liblber | OpenLDAP | OLDAP-2.8 |
| libsasl2 | Cyrus SASL | BSD-Attribution-HPND-disclaimer (CMU) |
| libcrypt | libxcrypt | LGPL-2.1-or-later |
| libkeyutils | keyutils | LGPL-2.1-or-later |
| libselinux | SELinux userspace | Public Domain |
| libpcre | PCRE | BSD-3-Clause |

Wer ausschließlich Bibliotheken des Betriebssystems verwenden möchte, kann statt
`psycopg[binary]` das reine Python-Paket mit einer System-libpq installieren
(`psycopg[c]` bzw. `psycopg` + `libpq5`).

## 3. Container-Basisabbilder

Die Abbilder des Projekts bauen auf diesen öffentlichen Abbildern auf; sie enthalten deren
Software unverändert. Darin sind auch Pakete unter GPL/LGPL enthalten (z. B. bash,
coreutils, glibc bzw. BusyBox, musl), die als eigenständige Programme im selben Abbild
liegen (bloße Zusammenstellung, keine Verknüpfung mit dem Code des Projekts). Wer Abbilder
weitergibt, sollte die Quellcode-Hinweise der Distribution beachten; die vollständige
Paketliste mit Lizenzen steht in der jeweiligen Abbild-SBOM.

| Abbild | genutzt für | Hauptkomponente / Lizenz | Distribution |
|---|---|---|---|
| `python:3.12.16-slim-trixie` (per Digest) | App-Abbild (Build + Laufzeit; die Laufzeitstufe ohne ungenutzte Pakete wie util-linux, apt, perl, DECISIONS K14) | CPython, PSF-2.0; zusätzlich `tini` (MIT) aus Debian | Debian 13, Paketlizenzen unter `/usr/share/doc/*/copyright` |
| `nginx:1.31.8-alpine` (per Digest) | Admin-Gateway und optionaler TLS-Proxy (beide ohne die Zusatzmodule acme/geoip/image-filter/njs/xslt und ohne curl) | nginx, BSD-2-Clause | Alpine Linux |
| `postgres:16.15-alpine` | Datenbank (unverändert aus Docker Hub gezogen, nicht Teil der Projekt-Abbilder) | PostgreSQL, PostgreSQL-Lizenz | Alpine Linux |

Nur für Entwicklung und Tests (nicht in ausgelieferten Abbildern): `axllent/mailpit`
(MIT), `node:22-alpine` bzw. `node:22.23.4-alpine` (Node.js, MIT; JS-Tests, Quill-Neubau), Playwright (Apache-2.0), Locust (MIT) sowie die Dev-Werkzeuge
aus `requirements-dev.lock` (pytest, ruff, mypy, httpx u. a.).
