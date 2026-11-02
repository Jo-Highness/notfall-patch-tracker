"""Nachbearbeitung der Syft-SBOMs (CycloneDX JSON) fuer scripts/sbom.sh (J15).

- ergaenzt die vendorten Browser-Bibliotheken (scripts/sbom-components.json), die Syft
  in minifizierten Dateien nicht erkennt, und prueft sie gegen die VERSION-Dateien,
- setzt Lizenzen fuer Pakete, deren Metadaten Syft nicht auswertet (license_overrides),
- setzt metadata.component (Anwendung, Version, Lizenz) fuer den Quellbaum,
- uebernimmt optional Lizenzen aus einer zweiten SBOM (Python-Pakete im App-Image),
- macht das Dokument reproduzierbar: Zeitstempel aus SOURCE_DATE_EPOCH, stabile
  Sortierung, serialNumber als UUIDv5 ueber den Inhalt.

Nur Standardbibliothek; laeuft im Python-Basisabbild (Wegwerf-Container).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Json = dict[str, Any]


def _base_purl(purl: str) -> str:
    return purl.split("?", 1)[0].split("#", 1)[0]


def _unversioned_purl(purl: str) -> str:
    return _base_purl(purl).rsplit("@", 1)[0]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundled_component(parent: Json, b: Json, rebuilt: Json | None, lock: Json) -> Json:
    """Unterkomponente fuer ein im Bundle von ``parent`` enthaltenes npm-Paket."""
    purl = f"pkg:npm/{b['name']}@{b['version']}"
    comp: Json = {
        "bom-ref": f"{purl}#bundled-in-{parent['name']}",
        "type": "library",
        "name": b["name"],
        "version": b["version"],
        "licenses": [{"license": {"id": b["license"]}}],
        "purl": purl,
    }
    if rebuilt:
        # Neubau: Version exakt laut Lockfile, Hash = Registry-Integrity des Pakets.
        entry = lock.get(f"node_modules/{b['name']}", {})
        if entry.get("version") != b["version"]:
            found = entry.get("version")
            raise SystemExit(f"{rebuilt['lockfile']}: {b['name']} {found} statt {b['version']}")
        alg, _, b64 = entry["integrity"].partition("-")
        if alg != "sha512":
            raise SystemExit(f"{rebuilt['lockfile']}: {b['name']} ohne sha512-Integrity")
        comp["hashes"] = [{"alg": "SHA-512", "content": base64.b64decode(b64).hex()}]
        basis = "lockfile"
    else:
        # Gebuendelt im Distributions-Build; die Version ist aus der Datei nicht
        # ablesbar und abgeleitet (Herleitung: _hinweis_bundled in sbom-components.json).
        basis = "abgeleitet"
    comp["properties"] = [
        {"name": "npt:bundled-in", "value": parent["purl"]},
        {"name": "npt:version-range", "value": b["range"]},
        {"name": "npt:version-basis", "value": basis},
    ]
    return comp


def vendor_components(spec: Json, root: Path) -> list[Json]:
    """Baut CycloneDX-Komponenten fuer die vendorten Bibliotheken und prueft sie."""
    out: list[Json] = []
    for c in spec["vendored"]:
        base = root / c["dir"]
        version_txt = (base / "VERSION").read_text(encoding="utf-8")
        if not version_txt.startswith(f"{c['name']} {c['version']} "):
            raise SystemExit(f"{base}/VERSION passt nicht zu {c['name']} {c['version']}")
        if not (base / "LICENSE").is_file():
            raise SystemExit(f"{base}/LICENSE fehlt")
        props: list[Json] = []
        occurrences: list[Json] = []
        for name in c["files"]:
            digest = _sha256(base / name)
            # Die VERSION-Datei dokumentiert die Pruefsumme jeder uebernommenen Datei.
            if digest not in version_txt:
                raise SystemExit(f"{base}/{name}: sha256 {digest} steht nicht in VERSION")
            props.append({"name": f"npt:file:{name}:sha256", "value": digest})
            occurrences.append({"location": f"{c['dir']}/{name}"})
        tarball = f"https://registry.npmjs.org/{c['name']}/-/{c['name']}-{c['version']}.tgz"
        integrity = base64.b64decode(c["npm_integrity_sha512"]).hex()
        comp: Json = {
            "bom-ref": c["purl"],
            "type": "library",
            "name": c["name"],
            "version": c["version"],
            "licenses": [{"license": {"id": c["license"]}}],
            "purl": c["purl"],
            "externalReferences": [
                {"type": "website", "url": c["homepage"]},
                {"type": "vcs", "url": c["vcs"]},
                {
                    "type": "distribution",
                    "url": tarball,
                    "hashes": [{"alg": "SHA-512", "content": integrity}],
                },
            ],
            "evidence": {"occurrences": occurrences},
            "properties": [{"name": "npt:vendored", "value": "true"}, *props],
        }
        rebuilt = c.get("rebuilt")
        lock: Json = {}
        if rebuilt:
            # Neubau per Rezept (K14): exakte Versionen und Integrity aus dessen Lockfile.
            lock = json.loads((root / rebuilt["lockfile"]).read_text(encoding="utf-8"))["packages"]
            if lock.get(f"node_modules/{c['name']}", {}).get("version") != c["version"]:
                raise SystemExit(f"{rebuilt['lockfile']}: {c['name']} {c['version']} fehlt")
            comp["pedigree"] = {"notes": rebuilt["notes"]}
            comp["properties"].append({"name": "npt:rebuilt-recipe", "value": rebuilt["recipe"]})
        bundled = [_bundled_component(c, b, rebuilt, lock) for b in c["bundled"]]
        if bundled:
            comp["components"] = bundled
        out.append(comp)
    return out


def license_map(sbom: Json) -> dict[str, list[Json]]:
    result: dict[str, list[Json]] = {}
    for comp in sbom.get("components", []):
        if comp.get("purl") and comp.get("licenses"):
            result.setdefault(_base_purl(comp["purl"]), comp["licenses"])
    return result


def remap_locations(comps: list[Json], loc_map: dict[str, str]) -> None:
    """Schreibt Pfadangaben (Datei-Komponenten, syft:location-Properties) um."""
    for comp in comps:
        if comp["type"] == "file" and comp["name"] in loc_map:
            comp["name"] = loc_map[comp["name"]]
        for prop in comp.get("properties", []):
            if prop["name"].startswith("syft:location:") and prop["value"] in loc_map:
                prop["value"] = loc_map[prop["value"]]


def fill_licenses(
    comps: list[Json], from_sbom: dict[str, list[Json]], overrides: dict[str, str]
) -> None:
    """Ergaenzt fehlende Lizenzen: zuerst aus der zweiten SBOM, dann aus license_overrides."""
    for comp in comps:
        purl = comp.get("purl", "")
        if comp.get("licenses") or not purl:
            continue
        found = from_sbom.get(_base_purl(purl))
        if found:
            comp["licenses"] = found
            continue
        spdx = overrides.get(_unversioned_purl(purl))
        if spdx:
            comp["licenses"] = [{"license": {"id": spdx}}]
            comp.setdefault("properties", []).append(
                {"name": "npt:license-source", "value": "scripts/sbom-components.json"}
            )


def _sort_key(comp: Json) -> tuple[str, str, str, str]:
    return (
        comp.get("purl", ""),
        comp.get("name", ""),
        comp.get("version", ""),
        comp.get("bom-ref", ""),
    )


def normalize(doc: Json, epoch: int) -> Json:
    doc.setdefault("metadata", {})["timestamp"] = datetime.fromtimestamp(epoch, UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    doc["components"] = sorted(doc.get("components", []), key=_sort_key)
    deps = doc.get("dependencies")
    if deps:
        for d in deps:
            if "dependsOn" in d:
                d["dependsOn"] = sorted(d["dependsOn"])
        doc["dependencies"] = sorted(deps, key=lambda d: d["ref"])
    doc.pop("serialNumber", None)
    canon = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canon.encode()).hexdigest()
    serial = uuid.uuid5(uuid.NAMESPACE_URL, f"npt-sbom:{digest}")
    return {
        "$schema": doc.pop("$schema", "http://cyclonedx.org/schema/bom-1.6.schema.json"),
        "bomFormat": doc.pop("bomFormat"),
        "specVersion": doc.pop("specVersion"),
        "serialNumber": f"urn:uuid:{serial}",
        **doc,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CycloneDX-SBOM nachbearbeiten (J15)")
    ap.add_argument("--in", dest="src", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--epoch", required=True, type=int, help="SOURCE_DATE_EPOCH")
    ap.add_argument("--extra", type=Path, required=True, help="scripts/sbom-components.json")
    ap.add_argument("--vendor", action="store_true", help="vendorte Bibliotheken ergaenzen")
    ap.add_argument("--root", type=Path, default=Path())
    ap.add_argument("--licenses-from", type=Path, help="SBOM, aus der Lizenzen uebernommen werden")
    ap.add_argument("--app-license", help="SPDX-Lizenz fuer metadata.component")
    ap.add_argument("--component-type", help="Typ fuer metadata.component (z. B. application)")
    ap.add_argument(
        "--location-map",
        action="append",
        default=[],
        metavar="ALT=NEU",
        help="Pfadangaben in syft:location-Properties umschreiben",
    )
    args = ap.parse_args(argv)

    doc: Json = json.loads(args.src.read_text(encoding="utf-8"))
    if doc.get("specVersion") != "1.6":
        raise SystemExit(f"{args.src}: CycloneDX 1.6 erwartet, gefunden {doc.get('specVersion')}")
    comps: list[Json] = doc.setdefault("components", [])

    extra: Json = json.loads(args.extra.read_text(encoding="utf-8"))
    remap_locations(comps, dict(m.split("=", 1) for m in args.location_map))
    if args.vendor:
        known = {_base_purl(c["purl"]) for c in comps if c.get("purl")}
        comps.extend(c for c in vendor_components(extra, args.root) if c["purl"] not in known)
    lic: dict[str, list[Json]] = {}
    if args.licenses_from:
        lic = license_map(json.loads(args.licenses_from.read_text(encoding="utf-8")))
    fill_licenses(comps, lic, extra.get("license_overrides", {}))

    meta_comp = doc.setdefault("metadata", {}).get("component")
    if meta_comp is not None:
        if args.component_type:
            meta_comp["type"] = args.component_type
        if args.app_license:
            meta_comp["licenses"] = [{"license": {"id": args.app_license}}]

    args.out.write_text(
        json.dumps(normalize(doc, args.epoch), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    pkgs = [c for c in comps if c["type"] != "file"]
    nested = sum(len(c.get("components", [])) for c in pkgs)
    missing = [c["name"] for c in pkgs if not c.get("licenses")]
    print(
        f"{args.out.name}: {len(comps)} Komponenten ({len(pkgs)} Pakete + {nested} gebuendelte, "
        f"{len(comps) - len(pkgs)} Dateien); ohne Lizenzangabe: {', '.join(missing) or '-'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
