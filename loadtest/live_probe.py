"""Unabhaengige Live-Latenz-Probe (nur Standardbibliothek, ein Prozess, zwei Threads).

Ein Thread haelt einen SSE-Stream offen, der andere schreibt jede ``--interval`` Sekunden
die Bemerkung eines festen Systems (``probe <n> <zeit>``). Gemessen wird Schreibbeginn ->
Eingang des ersten Delta-Frames, der diesen **oder einen neueren** Wert des Systems
enthaelt (Delta-Frames tragen den jeweils aktuellen Zeilenstand; ist ein Frame laenger
unterwegs als bis zum naechsten Schreiben, kommt direkt der neuere Wert – der Nutzer sieht
den aktuellen Stand, ``superseded`` zaehlt diese Faelle). Weil die Probe kaum CPU braucht,
trennt sie Server-Latenz von Messfehlern eines ausgelasteten Lastgenerators (Locust).

    python3 loadtest/live_probe.py --host http://127.0.0.1:8480 --project /p/<token> \\
        --duration 120 --interval 0.5
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import statistics
import threading
import time
import urllib.parse


def _conn(host: str) -> http.client.HTTPConnection:
    u = urllib.parse.urlsplit(host)
    return http.client.HTTPConnection(u.hostname or "127.0.0.1", u.port or 80, timeout=60)


def main() -> int:  # noqa: PLR0915 - lineares Messskript
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:8480")
    ap.add_argument("--project", required=True)
    ap.add_argument("--duration", type=float, default=120)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--xff", default="10.250.0.1")
    args = ap.parse_args()
    base = args.project.rstrip("/")
    headers = {"X-Forwarded-For": args.xff}

    c = _conn(args.host)
    c.request("GET", base + "/", headers=headers)
    html = c.getresponse().read().decode()
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    if not csrf:
        raise SystemExit("kein CSRF-Token (Projektpfad pruefen)")
    c.request("GET", base + "/api/systems", headers=headers)
    data = json.loads(c.getresponse().read())
    seq = data["seq"]
    sid = sorted(r["system_id"] for r in data["rows"])[0]
    current = next(r["remark"] for r in data["rows"] if r["system_id"] == sid)

    sent: dict[str, float] = {}
    latencies: list[float] = []
    stop = threading.Event()
    reader_end: dict[str, object] = {}
    lock = threading.Lock()  # ``sent`` wird von beiden Threads veraendert
    counters = {"frames": 0, "decode_errors": 0, "superseded": 0}
    t_start = time.perf_counter()

    def reader() -> None:
        s = _conn(args.host)
        s.request("GET", f"{base}/api/events?since={seq}", headers=headers)
        resp = s.getresponse()
        reader_end["status"] = resp.status
        while not stop.is_set():
            try:
                line = resp.fp.readline()
            except OSError as exc:
                reader_end.update(reason=repr(exc)[:80], at_s=round(time.perf_counter() - t_start))
                return
            if not line:
                reader_end.update(reason="eof", at_s=round(time.perf_counter() - t_start))
                return
            if line.startswith(b"data: "):
                now = time.perf_counter()
                counters["frames"] += 1
                try:
                    frame = json.loads(line[6:])
                except ValueError:
                    counters["decode_errors"] += 1
                    continue
                for row in frame.get("rows", ()):
                    if row.get("system_id") != sid:
                        continue
                    remark = row.get("remark") or ""
                    m = re.match(r"probe (\d+) ", remark)
                    if not m:
                        continue
                    k = int(m.group(1))
                    with lock:
                        for value in [v for v in sent if int(v.split()[1]) <= k]:
                            if value != remark:
                                counters["superseded"] += 1
                            latencies.append((now - sent.pop(value)) * 1000)

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    time.sleep(1.0)
    w = _conn(args.host)
    end = time.monotonic() + args.duration
    n = 0
    statuses: dict[int, int] = {}
    while time.monotonic() < end:
        n += 1
        value = f"probe {n} {time.time():.3f}"
        body = json.dumps({"field": "remark", "value": value, "expected": current})
        with lock:
            sent[value] = time.perf_counter()
        w.request(
            "PATCH",
            f"{base}/api/systems/{urllib.parse.quote(sid)}",
            body=body,
            headers={**headers, "Content-Type": "application/json", "X-CSRF-Token": csrf.group(1)},
        )
        r = w.getresponse()
        payload = json.loads(r.read())
        statuses[r.status] = statuses.get(r.status, 0) + 1
        if r.status != 200:
            with lock:
                sent.pop(value, None)  # nicht geschrieben -> nicht als verloren zaehlen
        current = (
            payload.get("row", {}).get("remark", value)
            if r.status == 200
            else payload.get("current", current)
        )
        time.sleep(args.interval)
    time.sleep(3)
    stop.set()
    lat = sorted(latencies)
    q = statistics.quantiles(lat, n=100) if len(lat) >= 2 else [0.0] * 99
    print(
        json.dumps(
            {
                "writes": n,
                "status": statuses,
                "received": len(lat),
                "lost": statuses.get(200, 0) - len(lat),
                "p50_ms": round(q[49]),
                "p95_ms": round(q[94]),
                "p99_ms": round(q[98]),
                "max_ms": round(lat[-1]) if lat else None,
                "stream": reader_end,
                **counters,
                "lost_at_s": sorted(round(t - t_start) for t in sent.values())[:20],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
