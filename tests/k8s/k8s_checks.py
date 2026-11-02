"""Pruefschritte fuer den kind-Test des Helm-Charts (tests/k8s/kind-test.sh, NPT-12).

Nur Standardbibliothek: laeuft im App-Abbild als Pod (Bearbeiter-Pruefungen direkt an
Pod-IPs) bzw. im E2E-Abbild im Docker-Netz ``kind`` (Admin ueber den NodePort).
Jede Unterfunktion gibt eine JSON-Zeile aus; Exitcode 0 = Erwartung erfuellt.

    python k8s_checks.py public --base http://10.244.1.5:8000 --path /p/<token>/
    python k8s_checks.py sse --a http://<pod-a>:8000 --b http://<pod-b>:8000 --path ...
    python k8s_checks.py sse-reconnect --a ... --b ... --svc http://npt-public:8000 --path ...
    python k8s_checks.py xff --base ... --path ... --xff 203.0.113.77
    python k8s_checks.py tcp --host 10.244.1.9 --port 8001
    python k8s_checks.py create-project --admin https://npt-admin.example.org:30443 --pki /pki
    python k8s_checks.py backups --admin ... --pki /pki --pid 1
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

TIMEOUT_S = 15.0
LIVE_MAX_MS = 2000


def out(result: dict[str, Any], ok: bool) -> int:
    result["ok"] = ok
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if ok else 1


# ---------------------------------------------------------------- Bearbeiter
class Public:
    def __init__(self, base: str, path: str, headers: dict[str, str] | None = None) -> None:
        self.base = base.rstrip("/")
        self.path = "/" + path.strip("/") + "/"
        self.headers = headers or {}
        self.csrf = ""

    def request(
        self, method: str, sub: str, body: Any = None, *, csrf: bool = True
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        headers = {**self.headers, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(  # noqa: S310 - Test-URL aus dem Aufruf
            self.base + self.path + sub, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, raw.decode("utf-8", "replace")

    def load_page(self) -> int:
        status, html = self.request("GET", "", csrf=False)
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', str(html))
        self.csrf = m.group(1) if m else ""
        return status

    def systems(self) -> dict[str, Any]:
        status, data = self.request("GET", "api/systems")
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(f"api/systems: {status}")
        return data

    def patch_remark(self, system_id: str, value: str, expected: Any) -> tuple[int, Any]:
        sid = urllib.parse.quote(system_id, safe="")
        return self.request(
            "PATCH", f"api/systems/{sid}", {"field": "remark", "value": value, "expected": expected}
        )


def first_row(pub: Public) -> dict[str, Any]:
    rows = pub.systems()["rows"]
    if not rows:
        raise RuntimeError("Projekt ohne Systeme")
    row: dict[str, Any] = rows[0]
    return row


def cmd_public(args: argparse.Namespace) -> int:
    pub = Public(args.base, args.path)
    res: dict[str, Any] = {"check": "public"}
    res["page"] = pub.load_page()
    data = pub.systems()
    res["systems"] = len(data["rows"])
    row = data["rows"][0]
    sid, current = row["system_id"], row.get("remark")
    value = f"k8s-{time.time_ns()}"
    res["patch"], _ = pub.patch_remark(sid, value, current)
    res["conflict"], body = pub.patch_remark(sid, value + "-x", current)
    res["conflict_current"] = body.get("current") if isinstance(body, dict) else None
    token, pub.csrf = pub.csrf, ""
    res["no_csrf"], _ = pub.patch_remark(sid, value + "-y", value)
    pub.csrf = token
    ok = (
        res["page"] == 200
        and res["systems"] > 0
        and res["patch"] == 200
        and res["conflict"] == 409
        and res["conflict_current"] == value
        and res["no_csrf"] == 403
    )
    return out(res, ok)


def sse_events(url: str, last_id: str | None, stop: threading.Event) -> Iterator[dict[str, Any]]:
    """Liest SSE-Frames (event/id/data) bis Verbindungsende oder ``stop``."""
    parts = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname or "", parts.port or 80, timeout=30)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    headers = {"Accept": "text/event-stream"}
    if last_id is not None:
        headers["Last-Event-ID"] = last_id
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    if resp.status != 200:
        raise RuntimeError(f"SSE {url}: {resp.status}")
    frame: dict[str, Any] = {}
    try:
        while not stop.is_set():
            line = resp.readline()
            if not line:
                return
            text = line.decode().rstrip("\r\n")
            if not text:
                if "data" in frame:
                    yield frame
                frame = {}
                continue
            if text.startswith(":"):
                yield {"event": "heartbeat"}
                continue
            key, _, value = text.partition(":")
            value = value.removeprefix(" ")
            if key == "data":
                frame["data"] = json.loads(value)
            elif key in ("event", "id"):
                frame[key] = value
    except (OSError, http.client.HTTPException):
        return
    finally:
        conn.close()


class Listener(threading.Thread):
    def __init__(self, url: str, last_id: str | None = None) -> None:
        super().__init__(daemon=True)
        self.url = url
        self.last_id = last_id
        self.stop = threading.Event()
        self.opened = threading.Event()
        self.closed = threading.Event()
        self.frames: list[tuple[float, dict[str, Any]]] = []
        self.error: str | None = None

    def run(self) -> None:
        try:
            for frame in sse_events(self.url, self.last_id, self.stop):
                self.opened.set()
                self.frames.append((time.monotonic(), frame))
                if "id" in frame:
                    self.last_id = frame["id"]
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.opened.set()
            self.closed.set()

    def wait_value(self, sid: str, value: str, timeout_s: float) -> float | None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for at, frame in list(self.frames):
                rows = frame.get("data", {}).get("rows") or []
                if any(r.get("system_id") == sid and r.get("remark") == value for r in rows):
                    return at
            time.sleep(0.02)
        return None


def cmd_sse(args: argparse.Namespace) -> int:
    """Stream an Pod A, Schreiben ueber Pod B -> Ereignis bei A in < 2 s (pod-uebergreifend)."""
    a, b = Public(args.a, args.path), Public(args.b, args.path)
    b.load_page()
    data = b.systems()
    row = data["rows"][0]
    listener = Listener(f"{a.base}{a.path}api/events?since={data['seq']}")
    listener.start()
    listener.opened.wait(10)
    time.sleep(0.5)
    value = f"sse-{time.time_ns()}"
    t0 = time.monotonic()
    status, _ = b.patch_remark(row["system_id"], value, row.get("remark"))
    at = listener.wait_value(row["system_id"], value, 5.0)
    listener.stop.set()
    ms = None if at is None else round((at - t0) * 1000)
    res = {"check": "sse", "patch": status, "latency_ms": ms, "error": listener.error}
    return out(res, status == 200 and ms is not None and ms < LIVE_MAX_MS)


def cmd_sse_reconnect(args: argparse.Namespace) -> int:
    """Stream an Pod A; A wird (vom Testskript) geloescht; waehrend der Trennung schreibt
    Pod B; Neuverbindung ueber den Service mit Last-Event-ID liefert die Aenderung nach."""
    a, b = Public(args.a, args.path), Public(args.b, args.path)
    b.load_page()
    data = b.systems()
    row = data["rows"][0]
    listener = Listener(f"{a.base}{a.path}api/events?since={data['seq']}")
    listener.start()
    listener.opened.wait(10)
    print("LISTENING", flush=True)
    if not listener.closed.wait(args.wait):
        return out({"check": "sse-reconnect", "error": "Stream an Pod A endete nicht"}, False)
    closed_at = time.monotonic()
    last_id = listener.last_id or str(data["seq"])
    value = f"gap-{time.time_ns()}"
    status, _ = b.patch_remark(row["system_id"], value, b.systems()["rows"][0].get("remark"))
    again = Listener(f"{args.svc.rstrip('/')}{a.path}api/events", last_id=last_id)
    again.start()
    at = again.wait_value(row["system_id"], value, 10.0)
    again.stop.set()
    res = {
        "check": "sse-reconnect",
        "last_id": last_id,
        "patch_during_gap": status,
        "caught_up": at is not None,
        "reconnect_s": None if at is None else round(at - closed_at, 2),
        "error": again.error,
    }
    return out(res, status == 200 and at is not None)


def cmd_xff(args: argparse.Namespace) -> int:
    pub = Public(args.base, args.path, {"X-Forwarded-For": args.xff})
    try:
        pub.load_page()
        row = first_row(pub)
        status, _ = pub.patch_remark(row["system_id"], f"xff-{time.time_ns()}", row.get("remark"))
    except OSError as exc:  # NetworkPolicy: Verbindung laeuft ins Leere
        reason = getattr(exc, "reason", exc)
        return out({"check": "xff", "error": f"{type(reason).__name__}: {reason}"}, False)
    return out({"check": "xff", "patch": status, "xff": args.xff}, status == 200)


def cmd_spoof_admin(args: argparse.Namespace) -> int:
    headers = {
        "X-SSL-Client-Verify": "SUCCESS",
        "X-SSL-Client-S-DN": "CN=Test Admin,OU=Admins,O=NPT Test,C=DE",
        "X-SSL-Client-Fingerprint": "0" * 40,
    }
    req = urllib.request.Request(args.base.rstrip("/") + "/admin/", headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310
            status = r.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    return out({"check": "spoof-admin", "status": status}, status == 404)


def cmd_tcp(args: argparse.Namespace) -> int:
    try:
        with socket.create_connection((args.host, args.port), timeout=args.timeout):
            state = "open"
    except ConnectionRefusedError:
        state = "refused"
    except TimeoutError:
        state = "timeout"
    except OSError as exc:
        state = type(exc).__name__
    return out({"check": "tcp", "target": f"{args.host}:{args.port}", "state": state}, True)


# ---------------------------------------------------------------------- Admin
class Admin:
    def __init__(self, base: str, pki: str) -> None:
        self.base = base.rstrip("/")
        ctx = ssl.create_default_context(cafile=f"{pki}/ca.crt")
        ctx.load_cert_chain(f"{pki}/admin.crt", f"{pki}/admin.key")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx), NoRedirect()
        )

    def request(
        self, path: str, form: dict[str, str] | None = None
    ) -> tuple[int, str, bytes, dict[str, str]]:
        data = None
        headers: dict[str, str] = {}
        if form is not None:
            _, _, html, _ = self.request("/admin/")
            m = re.search(rb'<meta name="csrf-token" content="([^"]+)"', html)
            if not m:
                raise RuntimeError("CSRF-Token fehlt")
            form = {"csrf_token": m.group(1).decode(), **form}
            data = urllib.parse.urlencode(form).encode()
            headers["Origin"] = self.base
        req = urllib.request.Request(self.base + path, data=data, headers=headers)  # noqa: S310
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.status, r.geturl(), r.read(), {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as exc:
            return exc.code, path, exc.read(), {k.lower(): v for k, v in exc.headers.items()}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def cmd_create_project(args: argparse.Namespace) -> int:
    admin = Admin(args.admin, args.pki)
    status, _, _, headers = admin.request(
        "/admin/projekte", {"name": args.name, "description": "kind-Test"}
    )
    loc = headers.get("location", "")
    m = re.search(r"/admin/projekte/(\d+)", loc)
    if status != 303 or not m:
        return out({"check": "create-project", "status": status, "location": loc}, False)
    pid = int(m.group(1))
    _, _, html, _ = admin.request(f"/admin/projekte/{pid}")
    url = re.search(rb'id="project-url" value="([^"]+)"', html)
    path = urllib.parse.urlsplit(url.group(1).decode()).path if url else ""
    return out({"check": "create-project", "pid": pid, "path": path}, bool(path))


def backup_ids(admin: Admin, pid: int) -> list[int]:
    _, _, html, _ = admin.request(f"/admin/projekte/{pid}")
    found = re.findall(rb"/admin/projekte/%d/backups/(\d+)/datei\.json" % pid, html)
    return sorted({int(x) for x in found})


def cmd_backups(args: argparse.Namespace) -> int:
    """Einstellungen (15 min), manuelles Backup, Download, Restore (NPT-12 Testplan 8)."""
    admin = Admin(args.admin, args.pki)
    pid = args.pid
    res: dict[str, Any] = {"check": "backups"}
    res["settings"], *_ = admin.request(
        f"/admin/projekte/{pid}/backups/einstellungen", {"interval": "15", "retention": "10"}
    )
    before = backup_ids(admin, pid)
    res["create"], *_ = admin.request(f"/admin/projekte/{pid}/backups/erstellen", {})
    after = backup_ids(admin, pid)
    new = sorted(set(after) - set(before))
    res["backup_ids"] = after
    if not new:
        return out(res, False)
    bid = new[-1]
    status, _, body, _ = admin.request(f"/admin/projekte/{pid}/backups/{bid}/datei.json")
    res["download_json"] = status
    res["download_valid"] = status == 200 and isinstance(json.loads(body), dict)
    res["download_xlsx"], *_ = admin.request(f"/admin/projekte/{pid}/backups/{bid}/datei.xlsx")
    if args.restore:
        res["restore"], *_ = admin.request(
            f"/admin/projekte/{pid}/backups/{bid}/wiederherstellen", {"confirm": "ja"}
        )
    ok = (
        res["settings"] == 303
        and res["create"] == 303
        and res["download_valid"]
        and res["download_xlsx"] == 200
        and res.get("restore", 303) == 303
    )
    return out(res, ok)


def cmd_download(args: argparse.Namespace) -> int:
    admin = Admin(args.admin, args.pki)
    ids = backup_ids(admin, args.pid)
    if not ids:
        return out({"check": "download", "backup_ids": []}, False)
    status, *_ = admin.request(f"/admin/projekte/{args.pid}/backups/{ids[-1]}/datei.json")
    return out({"check": "download", "backup_ids": ids, "status": status}, status == 200)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("public")
    p.add_argument("--base", required=True)
    p.add_argument("--path", required=True)
    p = sub.add_parser("sse")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--path", required=True)
    p = sub.add_parser("sse-reconnect")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--svc", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--wait", type=float, default=120.0)
    p = sub.add_parser("xff")
    p.add_argument("--base", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--xff", required=True)
    p = sub.add_parser("spoof-admin")
    p.add_argument("--base", required=True)
    p = sub.add_parser("tcp")
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--timeout", type=float, default=5.0)
    for name in ("create-project", "backups", "download"):
        p = sub.add_parser(name)
        p.add_argument("--admin", required=True)
        p.add_argument("--pki", required=True)
        p.add_argument("--name", default="kind-Test")
        p.add_argument("--pid", type=int, default=0)
        p.add_argument("--restore", action="store_true")
    args = ap.parse_args()
    handlers = {
        "public": cmd_public,
        "sse": cmd_sse,
        "sse-reconnect": cmd_sse_reconnect,
        "xff": cmd_xff,
        "spoof-admin": cmd_spoof_admin,
        "tcp": cmd_tcp,
        "create-project": cmd_create_project,
        "backups": cmd_backups,
        "download": cmd_download,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
