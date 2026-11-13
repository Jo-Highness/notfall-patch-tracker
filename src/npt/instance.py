"""Einstellungen der Instanz und Erstinbetriebnahme (NPT-15).

- Seitenname (Anzeige in allen Seiten), je Prozess kurz zwischengespeichert
- Einrichtungscode: nur als SHA-256 gespeichert, beim Start erzeugt und geloggt
- Entwurf des Installers (``setup_draft``) und Abschluss in EINER Transaktion
- Web-verwaltete Vertrauensanker (CA-Bundle, CRL) mit ``trust_version``

Alle Funktionen arbeiten in der Session des Aufrufers; Commit macht der Aufrufer,
ausser wo anders angegeben.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from cryptography import x509
from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from npt import limits, pki
from npt.audit import Actor, audit
from npt.config import IdKind
from npt.db.models import AdminUser, InstanceSettings
from npt.identity import identity_from_cert, normalize_identifier, normalize_issuer

DEFAULT_SITE_NAME = "Notfall-Patch-Tracker"
# Crockford-Base32 ohne verwechselbare Zeichen; 20 Zeichen = 100 Bit.
_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LEN = 20
SETUP_ACTOR = Actor.system("einrichtung")
DISPLAY_NAME_MAX = 200  # wie npt.admins


class SetupError(Exception):
    """Fachlicher Fehler; ``code`` ist ein fester Meldungsschluessel."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now() -> datetime:
    return datetime.now(tz=UTC)


# ------------------------------------------------------------------ Zeile
def get_row(session: Session, *, for_update: bool = False) -> InstanceSettings:
    stmt = select(InstanceSettings).where(InstanceSettings.id == 1)
    if for_update:
        # populate_existing: frisch aus der DB, nicht das (veraltete) Objekt der Session
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:  # Migration legt die Zeile an; Rueckfall fuer Tests/Altbestand
        row = InstanceSettings(id=1, site_name=DEFAULT_SITE_NAME, trust_version=0)
        session.add(row)
        session.flush()
    return row


def any_admin(session: Session) -> bool:
    return bool(session.execute(select(exists().where(AdminUser.id.is_not(None)))).scalar())


def setup_open(session: Session) -> bool:
    row = get_row(session)
    return row.setup_completed_at is None and not any_admin(session)


def _open_row(session: Session) -> InstanceSettings:
    """Zeile unter Sperre; Einrichtung muss noch offen sein (Race mit ``finish``)."""
    row = get_row(session, for_update=True)
    if row.setup_completed_at is not None or any_admin(session):
        raise SetupError("bereits_eingerichtet")
    return row


def close_if_admins(session: Session) -> bool:
    """Instanz mit Admins (z. B. per ADMIN_BOOTSTRAP_IDS) als eingerichtet markieren."""
    row = get_row(session, for_update=True)
    if row.setup_completed_at is None and any_admin(session):
        row.setup_completed_at = _now()
        row.setup_code_hash = None
        row.setup_draft = None
        return True
    return False


# ------------------------------------------------------------------ Seitenname
def clean_site_name(value: str) -> str:
    name = " ".join((value or "").split())
    if not name:
        raise SetupError("seitenname_fehlt")
    if len(name) > limits.SITE_NAME_MAX:
        raise SetupError("seitenname_zu_lang")
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in name):
        # Steuer- und Formatzeichen (u. a. Bidi-Overrides wie U+202E) gegen Spoofing
        raise SetupError("seitenname_ungueltig")
    return name


class SiteNameCache:
    """Seitenname je Prozess fuer ``SITE_NAME_CACHE_S`` Sekunden; Fehler -> Default."""

    def __init__(self, session_factory: Callable[[], Session] | None) -> None:
        self._factory = session_factory
        self._lock = threading.Lock()
        self._value = DEFAULT_SITE_NAME
        self._at = 0.0

    def invalidate(self) -> None:
        with self._lock:
            self._at = 0.0

    def get(self) -> str:
        now = time.monotonic()
        with self._lock:
            if self._factory is None or now - self._at < limits.SITE_NAME_CACHE_S:
                return self._value
        try:
            with self._factory() as session:
                value = session.execute(
                    select(InstanceSettings.site_name).where(InstanceSettings.id == 1)
                ).scalar_one_or_none()
                session.rollback()
        except Exception:  # Anzeige darf nie an der DB scheitern
            value = None
        with self._lock:
            if value:
                self._value = value
            self._at = now
            return self._value


# ------------------------------------------------------------------ Einrichtungscode
def normalize_code(value: str) -> str:
    return "".join(c for c in (value or "").upper() if c.isalnum())


def hash_code(code: str) -> str:
    return hashlib.sha256(normalize_code(code).encode("ascii", "ignore")).hexdigest()


def new_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))
    return "-".join(raw[i : i + 5] for i in range(0, _CODE_LEN, 5))


def ensure_setup_code(session: Session, *, rotate: bool = False) -> str | None:
    """Erzeugt einen Code, falls die Einrichtung offen ist und (noch) keiner existiert.

    Liefert den neuen Klartext-Code (zum einmaligen Ausgeben) oder ``None``.
    """
    row = get_row(session, for_update=True)
    if row.setup_completed_at is not None or any_admin(session):
        return None
    if row.setup_code_hash and not rotate:
        return None
    code = new_code()
    row.setup_code_hash = hash_code(code)
    row.updated_at = _now()
    return code


def check_code(session: Session, code: str) -> bool:
    row = get_row(session)
    if not row.setup_code_hash or row.setup_completed_at is not None:
        return False
    norm = normalize_code(code)
    if len(norm) != _CODE_LEN:
        return False
    return hmac.compare_digest(hash_code(norm), row.setup_code_hash)


# ------------------------------------------------------------------ Entwurf
def draft(row: InstanceSettings) -> dict[str, Any]:
    d = dict(row.setup_draft or {})
    d.setdefault("site_name", row.site_name or DEFAULT_SITE_NAME)
    d.setdefault("ca_pem", None)
    d.setdefault("ca_external", False)
    d.setdefault("crl_pem", None)
    d.setdefault("admin", None)
    return d


def _save_draft(row: InstanceSettings, d: dict[str, Any]) -> None:
    row.setup_draft = dict(d)  # neues Objekt -> SQLAlchemy erkennt die Aenderung
    row.updated_at = _now()


def set_site_name(session: Session, value: str) -> None:
    row = _open_row(session)
    d = draft(row)
    d["site_name"] = clean_site_name(value)
    _save_draft(row, d)


def _check_crl_fits(crl_pem: str | None, bundle: list[x509.Certificate]) -> None:
    """Gespeicherte CRL muss zum (neuen) Bundle passen und es vollstaendig abdecken."""
    if not crl_pem:
        return
    crls = pki.load_crls_pem(crl_pem)
    if (
        not bundle
        or pki.crls_missing_for(bundle, crls)
        or any(not any(c.subject == crl.issuer for c in bundle) for crl in crls)
    ):
        # Nicht still verwerfen (gesperrte Zertifikate gaelten sonst wieder).
        raise SetupError("crl_passt_nicht")


def set_ca(session: Session, data: bytes | None, *, external: bool) -> None:
    row = _open_row(session)
    d = draft(row)
    if external:
        if d.get("crl_pem"):
            raise SetupError("crl_passt_nicht")
        d["ca_pem"], d["ca_external"] = None, True
    else:
        if not data:
            raise SetupError("datei_leer")
        certs = pki.parse_ca_bundle(data)
        _check_crl_fits(d.get("crl_pem"), certs)
        d["ca_pem"], d["ca_external"] = pki.to_pem(certs), False
    # Die Admin-Pruefung haengt am Bundle -> neu angeben
    d["admin"] = None
    _save_draft(row, d)


def parse_complete_crls(data: bytes, bundle: list[x509.Certificate]) -> str:
    crls = pki.parse_crls(data, bundle)
    if pki.crls_missing_for(bundle, crls):
        raise SetupError("crl_unvollstaendig")
    return pki.crl_pem(crls)


def set_crl(session: Session, data: bytes | None) -> None:
    row = _open_row(session)
    d = draft(row)
    if not data:
        d["crl_pem"] = None
    else:
        bundle = pki.load_bundle_pem(d.get("ca_pem"))
        if not bundle:
            raise SetupError("erst_ca")
        d["crl_pem"] = parse_complete_crls(data, bundle)
        adm = d.get("admin")
        if adm and adm.get("serial") and adm.get("source") == "cert":
            for crl in pki.load_crls_pem(d["crl_pem"]):
                if crl.issuer.rfc4514_string() == adm["issuer_dn"] and (
                    crl.get_revoked_certificate_by_serial_number(int(adm["serial"], 16))
                ):
                    raise SetupError("zertifikat_gesperrt")
    _save_draft(row, d)


def admin_from_cert(
    cert: x509.Certificate,
    kind: IdKind,
    bundle: list[x509.Certificate],
    max_depth: int,
    crls: list[x509.CertificateRevocationList] | None = None,
) -> dict[str, Any]:
    try:
        ident = identity_from_cert(cert, kind)
    except pki.CRYPTO_ERRORS as exc:
        raise SetupError("zertifikat_defekt") from exc
    if not ident.candidates:
        raise SetupError("kein_identifier")
    depth = pki.verify_chain(cert, bundle) if bundle else None
    if bundle and depth is None:
        raise SetupError("nicht_von_ca")
    if depth is not None and depth > max_depth:
        raise SetupError("kette_zu_tief")
    if crls and pki.is_revoked(cert, crls):
        raise SetupError("zertifikat_gesperrt")
    return {
        "source": "cert",
        "id_kind": kind,
        "identifier": ident.primary,
        "issuer_norm": ident.issuer_norm,
        "issuer_dn": ident.issuer_dn,
        "subject_dn": ident.subject_dn,
        "sha256": ident.fingerprint_sha256,
        "serial": ident.serial_hex,
        "chain_depth": depth,
    }


def set_admin_cert(
    session: Session, kind: IdKind, data: bytes, display_name: str, max_depth: int
) -> None:
    row = _open_row(session)
    d = draft(row)
    bundle = pki.load_bundle_pem(d.get("ca_pem"))
    if not bundle and not d.get("ca_external"):
        raise SetupError("erst_ca")
    cert = pki.parse_client_cert(data)
    entry = admin_from_cert(cert, kind, bundle, max_depth, pki.load_crls_pem(d.get("crl_pem")))
    entry["display_name"] = _clean_name(display_name)
    d["admin"] = entry
    _save_draft(row, d)


def set_admin_manual(
    session: Session,
    kind: IdKind,
    identifier: str,
    issuer: str,
    display_name: str,
    *,
    any_issuer: bool = False,
) -> None:
    row = _open_row(session)
    d = draft(row)
    if not issuer.strip() and not any_issuer:
        # '*' gilt fuer JEDE CA des Gateways (auch spaeter ergaenzte) -> nur ausdruecklich
        raise SetupError("aussteller_fehlt")
    try:
        ident = normalize_identifier(kind, identifier)
        issuer_norm = normalize_issuer("" if any_issuer and not issuer.strip() else issuer)
    except ValueError as exc:
        raise SetupError("identifier_ungueltig") from exc
    if not ident:
        raise SetupError("identifier_ungueltig")
    d["admin"] = {
        "source": "manual",
        "id_kind": kind,
        "identifier": ident,
        "issuer_norm": issuer_norm,
        "issuer_dn": issuer.strip() or "*",
        "subject_dn": None,
        "sha256": None,
        "chain_depth": None,
        "display_name": _clean_name(display_name),
    }
    _save_draft(row, d)


def _clean_name(value: str) -> str | None:
    v = " ".join((value or "").split())
    if len(v) > DISPLAY_NAME_MAX:
        raise SetupError("name_zu_lang")
    return v or None


def missing(d: dict[str, Any], kind: IdKind) -> list[str]:
    """Fehlende Pflichtangaben (Schluessel fuer die Anzeige)."""
    out: list[str] = []
    if not d.get("site_name"):
        out.append("seitenname")
    if not d.get("ca_pem") and not d.get("ca_external"):
        out.append("ca")
    adm = d.get("admin")
    if not adm:
        out.append("admin")
    elif adm.get("id_kind") != kind:
        out.append("admin_art")  # ADMIN_CERT_ID_ATTRIBUTE wurde seitdem geaendert
    return out


def finish(session: Session, kind: IdKind, client_ip: str | None = None) -> None:
    """Einrichtung abschliessen: alles in der Transaktion des Aufrufers."""
    row = get_row(session, for_update=True)
    if row.setup_completed_at is not None or any_admin(session):
        raise SetupError("bereits_eingerichtet")
    d = draft(row)
    if missing(d, kind):
        raise SetupError("unvollstaendig")
    adm = d["admin"]
    row.site_name = clean_site_name(d["site_name"])
    if d.get("ca_pem"):
        row.admin_ca_pem = d["ca_pem"]
        row.admin_crl_pem = d.get("crl_pem")
        row.trust_version = (row.trust_version or 0) + 1
    actor = Actor(SETUP_ACTOR.issuer, SETUP_ACTOR.identifier, client_ip)
    _add_first_admin(session, kind, adm, actor)
    audit(
        session,
        actor,
        "einrichtung_abgeschlossen",
        target=row.site_name,
        details={
            "ca": pki_summary(d.get("ca_pem")),
            "ca_extern": bool(d.get("ca_external")),
            "crl": bool(d.get("crl_pem")),
            "admin_quelle": adm.get("source"),
        },
    )
    row.setup_completed_at = _now()
    row.setup_code_hash = None
    row.setup_draft = None
    row.updated_at = _now()


def _add_first_admin(
    session: Session, kind: IdKind, adm: dict[str, Any], actor: Actor = SETUP_ACTOR
) -> None:
    """Ersten Admin anlegen (wie ``npt.admins.add_admin``, ohne das Admin-Modul, NPT-02)."""
    try:
        ident = normalize_identifier(kind, adm["identifier"])
        issuer = normalize_issuer(adm["issuer_norm"] if adm["issuer_norm"] != "*" else "")
    except ValueError as exc:
        raise SetupError("identifier_ungueltig") from exc
    new_id = session.execute(
        insert(AdminUser)
        .values(
            issuer_norm=issuer,
            id_kind=kind,
            identifier_norm=ident,
            display_name=adm.get("display_name"),
            created_by=SETUP_ACTOR.identifier,
        )
        .on_conflict_do_nothing(constraint="uq_admin_users_identity")
        .returning(AdminUser.id)
    ).scalar_one_or_none()
    if new_id is None:
        raise SetupError("existiert_bereits")
    audit(
        session,
        actor,
        "admin_hinzugefuegt",
        target=ident,
        details={"issuer": issuer, "kind": kind, "display_name": adm.get("display_name")},
    )


def pki_summary(pem: str | None) -> list[dict[str, str]]:
    return [
        {"subject": i.subject, "sha256": i.sha256}
        for i in (pki.info(c) for c in pki.load_bundle_pem(pem))
    ]


# ------------------------------------------------------------------ Admin-Einstellungen
def update_site_name(session: Session, actor: Actor, value: str) -> None:
    row = get_row(session, for_update=True)
    name = clean_site_name(value)
    if name == row.site_name:
        return
    audit(session, actor, "seitenname_geaendert", target=name, details={"vorher": row.site_name})
    row.site_name = name
    row.updated_at = _now()


def own_cert_accepted(
    own: x509.Certificate | None,
    bundle: list[x509.Certificate],
    crls: list[x509.CertificateRevocationList],
    max_depth: int,
) -> bool:
    """Wuerde das Gateway das eigene Zertifikat ueber die Web-CA noch akzeptieren?"""
    if own is None or not bundle:
        return False
    depth = pki.verify_chain(own, bundle)
    if depth is None or depth > max_depth:
        return False
    return not (crls and (pki.crls_missing_for(bundle, crls) or pki.is_revoked(own, crls)))


def replace_ca(
    session: Session,
    actor: Actor,
    data: bytes | None,
    own_cert: x509.Certificate | None,
    *,
    confirm_lockout: bool,
    max_depth: int,
) -> None:
    """Web-CA ersetzen (``data``) oder entfernen (``None``), mit Aussperrschutz."""
    row = get_row(session, for_update=True)
    certs = pki.parse_ca_bundle(data) if data else []
    if row.admin_crl_pem:
        _check_crl_fits(row.admin_crl_pem, certs)
    crls = pki.load_crls_pem(row.admin_crl_pem)
    if not own_cert_accepted(own_cert, certs, crls, max_depth) and not confirm_lockout:
        raise SetupError("aussperrung")
    old = pki_summary(row.admin_ca_pem)
    row.admin_ca_pem = pki.to_pem(certs) if certs else None
    row.trust_version = (row.trust_version or 0) + 1
    row.updated_at = _now()
    audit(
        session,
        actor,
        "admin_ca_ersetzt" if certs else "admin_ca_entfernt",
        details={"vorher": old, "nachher": pki_summary(row.admin_ca_pem)},
    )


def replace_crl(
    session: Session,
    actor: Actor,
    data: bytes | None,
    own_cert: x509.Certificate | None = None,
    *,
    confirm_lockout: bool = False,
    max_depth: int = 2,
) -> None:
    row = get_row(session, for_update=True)
    bundle = pki.load_bundle_pem(row.admin_ca_pem)
    if data:
        if not bundle:
            raise SetupError("erst_ca")
        new_pem = parse_complete_crls(data, bundle)
        crls = pki.load_crls_pem(new_pem)
        own_was_ok = own_cert_accepted(
            own_cert, bundle, pki.load_crls_pem(row.admin_crl_pem), max_depth
        )
        if (
            own_was_ok
            and not own_cert_accepted(own_cert, bundle, crls, max_depth)
            and not confirm_lockout
        ):
            raise SetupError("aussperrung_crl")
        row.admin_crl_pem = new_pem
        details: dict[str, Any] = {
            "crls": [
                {
                    "issuer": c.issuer.rfc4514_string(),
                    "naechste_aktualisierung": (
                        c.next_update_utc.isoformat() if c.next_update_utc else None
                    ),
                    "gesperrt": len(c),
                }
                for c in crls
            ]
        }
    else:
        row.admin_crl_pem = None
        details = {}
    row.trust_version = (row.trust_version or 0) + 1
    row.updated_at = _now()
    audit(session, actor, "admin_crl_ersetzt" if data else "admin_crl_entfernt", details=details)


def reset_trust(session: Session, actor: Actor) -> bool:
    """Notfall: Web-CA und Web-CRL leeren (CLI ``python -m npt.trust_reset``)."""
    row = get_row(session, for_update=True)
    if not row.admin_ca_pem and not row.admin_crl_pem:
        return False
    old = pki_summary(row.admin_ca_pem)
    row.admin_ca_pem = None
    row.admin_crl_pem = None
    row.trust_version = (row.trust_version or 0) + 1
    row.updated_at = _now()
    audit(session, actor, "vertrauensanker_zurueckgesetzt", details={"vorher": old})
    return True


def record_gateway_cert(session: Session, sha256: str | None) -> None:
    session.execute(
        update(InstanceSettings)
        .where(InstanceSettings.id == 1)
        .where(InstanceSettings.gateway_cert_sha256.is_distinct_from(sha256))
        .values(gateway_cert_sha256=sha256)
    )
