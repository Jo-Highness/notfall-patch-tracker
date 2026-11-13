"""Vertrauensanker DB -> TRUST_DIR fuer das Gateway (NPT-15).

Je Tick:
- ``ca-bundle.pem`` und ``crl.pem`` aus ``instance_settings`` schreiben bzw. entfernen
  (nur bei abweichendem Inhalt; atomar: tmp-Datei, fsync, rename)
- fehlt ein Serverzertifikat (``server.crt``/``server.key``), ein selbstsigniertes
  erzeugen (Schluessel nur im Verzeichnis, nie in der DB) und den SHA-256-Fingerprint
  zur Anzeige in der DB vermerken

Das Gateway liest das Verzeichnis read-only und laedt bei Inhaltsaenderung neu.
Mehrere Jobs-Instanzen auf getrennten Verzeichnissen (Kubernetes: je Backend-Pod ein
emptyDir) sind unkritisch.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509
from sqlalchemy.orm import Session

from npt import instance, pki
from npt.config import Settings

log = logging.getLogger("npt.jobs.trust")

CA_FILE = "ca-bundle.pem"
CRL_FILE = "crl.pem"
CERT_FILE = "server.crt"
KEY_FILE = "server.key"


def write_atomic(path: Path, content: str, mode: int = 0o644) -> bool:
    """Schreibt nur bei geaendertem Inhalt; ``True`` bei Aenderung."""
    data = content.encode("ascii")
    try:
        if path.read_bytes() == data:
            return False
    except FileNotFoundError:
        pass
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, mode)  # umask-unabhaengig
    os.replace(tmp, path)
    return True


def remove(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def hostnames(settings: Settings) -> list[str]:
    names: list[str] = []
    for url in (settings.ADMIN_BASE_URL, settings.PUBLIC_BASE_URL):
        host = urlsplit(url).hostname if url else None
        if host:
            names.append(host)
    names.append("localhost")
    return names


def ensure_server_cert(directory: Path, settings: Settings) -> str | None:
    cert_path, key_path = directory / CERT_FILE, directory / KEY_FILE
    if not (cert_path.exists() and key_path.exists()):
        cert_pem, key_pem, _fp = pki.generate_self_signed(hostnames(settings))
        # Erst der Schluessel, dann das Zertifikat: das Gateway wartet auf beide.
        write_atomic(key_path, key_pem, 0o640)
        write_atomic(cert_path, cert_pem, 0o644)
        log.info(
            "Selbstsigniertes Gateway-Zertifikat erzeugt", extra={"hosts": hostnames(settings)}
        )
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (OSError, ValueError):
        return None
    return pki.sha256_hex(cert)


def sync(session: Session, directory: Path, settings: Settings) -> bool:
    """Ein Abgleich; liefert ``True``, wenn sich eine Datei geaendert hat."""
    row = instance.get_row(session)
    changed = False
    if row.admin_ca_pem:
        changed |= write_atomic(directory / CA_FILE, row.admin_ca_pem)
    else:
        changed |= remove(directory / CA_FILE)
    if row.admin_crl_pem and row.admin_ca_pem:
        changed |= write_atomic(directory / CRL_FILE, row.admin_crl_pem)
    else:
        changed |= remove(directory / CRL_FILE)
    fp = ensure_server_cert(directory, settings)
    instance.record_gateway_cert(session, fp)
    session.commit()
    if changed:
        log.info(
            "Vertrauensanker fuer das Gateway aktualisiert", extra={"version": row.trust_version}
        )
    return changed


def make_job(
    settings: Settings, session_factory: Callable[[], Session]
) -> Callable[[], None] | None:
    if not settings.TRUST_DIR:
        return None
    directory = Path(settings.TRUST_DIR)

    def job() -> None:
        if not directory.is_dir():
            log.warning("TRUST_DIR fehlt", extra={"pfad": str(directory)})
            return
        with session_factory() as session:
            try:
                sync(session, directory, settings)
            finally:
                if session.in_transaction():
                    session.rollback()

    return job
