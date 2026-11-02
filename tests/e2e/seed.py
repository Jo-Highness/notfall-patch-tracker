"""E2E-Vorbereitung: Stammdaten (2.000 generierte Testsysteme) ueber den Admin-Bereich
importieren – genau wie ein Admin (Upload -> Vorschau -> Uebernehmen, mTLS-Gateway).

    python -m tests.e2e.seed --admin https://localhost:8491 --pki /pki

Wiederholbar: Ist der Bestand bereits gleich, aendert die Uebernahme nichts (gleiche
Testdaten per festem Seed). Exitcode 0 bei Erfolg.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from tests.e2e.live_smoke import Admin

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def multipart(fields: dict[str, str], file_field: str, name: str, data: bytes) -> tuple[bytes, str]:
    boundary = "npt" + secrets.token_hex(12)
    out = bytearray()
    for key, value in fields.items():
        out += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
        ).encode()
    out += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="{name}"\r\nContent-Type: {XLSX_MIME}\r\n\r\n'
    ).encode()
    out += data + f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin", default="https://localhost:8481")
    ap.add_argument("--pki", default="pki-out/clients")
    ap.add_argument("--systems", type=int, default=2000)
    args = ap.parse_args()
    admin = Admin(args.admin, args.pki)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "stammdaten.xlsx"
        subprocess.run(  # festes Skript aus dem Repo
            [sys.executable, "scripts/gen-testdata.py", "--systems", str(args.systems),
             "--out", str(out)],
            check=True,
            stdout=subprocess.DEVNULL,
        )  # fmt: skip
        data = out.read_bytes()
    _, html = admin.get("/admin/stammdaten")
    body, ctype = multipart({"csrf_token": admin.csrf(html)}, "datei", "stammdaten.xlsx", data)
    req = urllib.request.Request(  # noqa: S310 - feste https-URL
        admin.base + "/admin/stammdaten/upload",
        data=body,
        headers={"Origin": admin.base, "Content-Type": ctype},
    )
    with admin.opener.open(req, timeout=120) as r:
        url, preview = r.geturl(), r.read().decode()
    m = re.search(r"/admin/stammdaten/vorschau/([A-Za-z0-9_\-]+)", url)
    assert m, f"keine Vorschau ({url})"
    stage = m.group(1)
    fp = re.search(r'name="fingerprint" value="([^"]+)"', preview)
    assert fp, "Fingerprint fehlt"
    url, _ = admin.post(
        f"/admin/stammdaten/vorschau/{stage}/uebernehmen", {"fingerprint": fp.group(1)}
    )
    ok = "fehler" not in urllib.parse.urlsplit(url).query
    print(json.dumps({"stage": stage, "after": urllib.parse.urlsplit(url).path, "ok": ok}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
