"""Fasst einen Lasttest-Lauf zusammen (Locust-CSV, docker stats, Browser-Probe) als JSON.

    python3 loadtest/summarize.py <ausgabeverzeichnis> [container-muster]

Nur Standardbibliothek (laeuft direkt auf dem Docker-Host).
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_UNITS = {"b": 1e-6, "kib": 1 / 1024, "mib": 1.0, "gib": 1024.0, "kb": 1e-3, "mb": 1.0, "gb": 1e3}


def _mib(text: str) -> float:
    m = re.match(r"([\d.]+)\s*([a-zA-Z]+)", text.strip())
    if not m:
        return 0.0
    return float(m.group(1)) * _UNITS.get(m.group(2).lower(), 1.0)


def locust_stats(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            n = int(row["Request Count"] or 0)
            fails = int(row["Failure Count"] or 0)
            out[row["Name"]] = {
                "type": row["Type"],
                "count": n,
                "failures": fails,
                "failure_rate_pct": round(100 * fails / n, 3) if n else 0.0,
                "rps": round(float(row["Requests/s"] or 0), 2),
                "p50_ms": row["50%"],
                "p95_ms": row["95%"],
                "p99_ms": row["99%"],
                "max_ms": round(float(row["Max Response Time"] or 0)),
                "avg_ms": round(float(row["Average Response Time"] or 0), 1),
                "avg_bytes": round(float(row["Average Content Size"] or 0)),
            }
    return out


def docker_stats(path: Path, match: str) -> dict[str, Any]:
    cpu: dict[str, list[float]] = defaultdict(list)
    mem: dict[str, list[float]] = defaultdict(list)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            parts = line.strip().split(",")
            if len(parts) < 4:
                continue
            _, name, cpu_pct, mem_usage = parts[:4]
            try:
                cpu_value = float(cpu_pct.rstrip("%"))
            except ValueError:  # "--" waehrend Start/Stopp eines Containers
                continue
            cpu[name].append(cpu_value)
            mem[name].append(_mib(mem_usage.split("/")[0]))
    result: dict[str, Any] = {}
    for name in sorted(cpu):
        values = cpu[name]
        result[name] = {
            "relevant": match in name or name == "npt-locust",
            "samples": len(values),
            "cpu_avg_pct": round(sum(values) / len(values), 1),
            "cpu_max_pct": round(max(values), 1),
            "mem_avg_mib": round(sum(mem[name]) / len(mem[name])),
            "mem_max_mib": round(max(mem[name])),
        }
    return result


def main() -> int:
    out = Path(sys.argv[1])
    match = sys.argv[2] if len(sys.argv) > 2 else "npt-dev"
    summary: dict[str, Any] = {"locust": locust_stats(out / "lt_stats.csv")}
    failures = out / "lt_failures.csv"
    if failures.exists():
        with failures.open(newline="", encoding="utf-8") as fh:
            summary["failures"] = [
                {"name": r["Name"], "error": r["Error"][:160], "count": int(r["Occurrences"])}
                for r in csv.DictReader(fh)
            ]
    summary["containers"] = docker_stats(out / "docker-stats.csv", match)
    probe = out / "live_probe.json"
    if probe.exists() and probe.stat().st_size:
        summary["live_probe"] = json.loads(probe.read_text(encoding="utf-8"))
    browser = out / "browser.json"
    if browser.exists() and browser.stat().st_size:
        summary["browser"] = json.loads(browser.read_text(encoding="utf-8"))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
