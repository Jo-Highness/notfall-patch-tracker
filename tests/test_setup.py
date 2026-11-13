"""Erstinbetriebnahme (/einrichtung), Admin-Einstellungen und Vertrauensanker (NPT-15)."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from sqlalchemy import select

from npt import instance, pki, ratelimit
from npt.db.models import AdminAuditLog, AdminUser, InstanceSettings
from tests.admin_helpers import ADMIN_DN, AdminClient, bootstrap
from tests.certs import CertFactory
from tests.conftest import PUBLIC_ORIGIN, make_settings

NOW = dt.datetime.now(dt.UTC)
# Zusammengesetzt, damit der Secret-Scanner (gitleaks) keinen Schluessel vermutet.
KEY_BEGIN = b"-----BEGIN" + b" "


def pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def make_ca(
    name: str,
    *,
    issuer: tuple[x509.Certificate, Any] | None = None,
    ca: bool = True,
    days: int = 30,
) -> tuple[x509.Certificate, Any]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    issuer_name, issuer_key = (issuer[0].subject, issuer[1]) if issuer else (subject, key)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - dt.timedelta(days=2))
        .not_valid_after(NOW + dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .sign(issuer_key, hashes.SHA256())
    )
    return cert, key


def make_crl(
    issuer: tuple[x509.Certificate, Any], *, next_days: int = 7, revoked: int | None = None
) -> bytes:
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(issuer[0].subject)
        .last_update(NOW - dt.timedelta(days=1))
        .next_update(NOW + dt.timedelta(days=next_days))
    )
    if revoked is not None:
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(revoked)
            .revocation_date(NOW - dt.timedelta(hours=1))
            .build()
        )
    return builder.sign(issuer[1], hashes.SHA256()).public_bytes(serialization.Encoding.PEM)


# ================================================================ pki
class TestPki:
    def test_bundle_root_and_intermediate(self) -> None:
        root = make_ca("Root")
        inter = make_ca("Inter", issuer=root)
        certs = pki.parse_ca_bundle(pem(inter[0]) + pem(root[0]) + pem(root[0]))
        assert len(certs) == 2  # Duplikat entfernt
        leaf = make_ca("Leaf", issuer=inter, ca=False)[0]
        assert pki.verify_chain(leaf, certs) == 2
        assert pki.verify_chain(make_ca("Leaf2", issuer=root, ca=False)[0], certs) == 1
        assert pki.verify_chain(make_ca("Fremd", ca=False)[0], certs) is None

    @pytest.mark.parametrize(
        ("data", "code"),
        [
            (b"", "datei_leer"),
            (KEY_BEGIN + b"PRIVATE KEY-----\nabc\n", "privater_schluessel"),
            (KEY_BEGIN + b"ENCRYPTED PRIVATE KEY-----\n", "privater_schluessel"),
            (b"-----BEGIN PUBLIC KEY-----\nabc\n-----END PUBLIC KEY-----\n", "kein_zertifikat"),
            (b"\x30\x82\x01\x00garbage", "kein_zertifikat"),
        ],
    )
    def test_bundle_rejects(self, data: bytes, code: str) -> None:
        with pytest.raises(pki.PkiError) as exc:
            pki.parse_ca_bundle(data)
        assert exc.value.code == code

    def test_bundle_rejects_leaf_expired_and_missing_root(self) -> None:
        root = make_ca("Root")
        with pytest.raises(pki.PkiError, match="keine_ca"):
            pki.parse_ca_bundle(pem(root[0]) + pem(make_ca("L", issuer=root, ca=False)[0]))
        with pytest.raises(pki.PkiError, match="ca_abgelaufen"):
            pki.parse_ca_bundle(pem(make_ca("Alt", days=-1)[0]))
        with pytest.raises(pki.PkiError, match="keine_root"):
            pki.parse_ca_bundle(pem(make_ca("Inter", issuer=root)[0]))

    def test_bundle_size_and_count(self) -> None:
        root = make_ca("Root")
        many = b"".join(pem(make_ca(f"I{i}", issuer=root)[0]) for i in range(21))
        with pytest.raises(pki.PkiError, match="zu_viele_zertifikate"):
            pki.parse_ca_bundle(pem(root[0]) + many)
        with pytest.raises(pki.PkiError, match="datei_zu_gross"):
            pki.parse_ca_bundle(b"x" * (300 * 1024))

    def test_der_single_cert(self) -> None:
        root = make_ca("Root")
        der = root[0].public_bytes(serialization.Encoding.DER)
        assert pki.parse_ca_bundle(der)[0] == root[0]

    def test_pkcs12_rejected_as_client_cert(self, certs: CertFactory) -> None:
        cert, key = make_ca("Mit Schluessel", ca=False)
        p12 = pkcs12.serialize_key_and_certificates(
            b"x", key, cert, None, serialization.NoEncryption()
        )
        with pytest.raises(pki.PkiError, match="kein_zertifikat"):
            pki.parse_client_cert(p12)
        with pytest.raises(pki.PkiError, match="ist_ca"):
            pki.parse_client_cert(pem(certs.ca_cert))

    def test_crl(self) -> None:
        root = make_ca("Root")
        other = make_ca("Other")
        bundle = [root[0]]
        assert pki.parse_crl(make_crl(root, revoked=5), bundle).issuer == root[0].subject
        with pytest.raises(pki.PkiError, match="crl_fremd"):
            pki.parse_crl(make_crl(other), bundle)
        with pytest.raises(pki.PkiError, match="crl_abgelaufen"):
            pki.parse_crl(make_crl(root, next_days=-1), bundle)
        with pytest.raises(pki.PkiError, match="keine_crl"):
            pki.parse_crl(pem(root[0]), bundle)

    def test_self_signed(self) -> None:
        cert_pem, key_pem, fp = pki.generate_self_signed(["npt.example", "10.0.0.1"])
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
        assert pki.sha256_hex(cert) == fp
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        assert san.get_values_for_type(x509.DNSName) == ["npt.example"]
        assert "PRIVATE KEY" in key_pem


# ================================================================ Installer
class Setup:
    """Treibt /einrichtung wie ein Browser (Origin = PUBLIC_BASE_URL)."""

    def __init__(self, client: Any, code: str) -> None:
        self.c = client
        self.code = code

    def post(self, aktion: str, files: dict[str, Any] | None = None, **data: str) -> httpx.Response:
        form = {"code": self.code, "aktion": aktion, **data}
        r: httpx.Response = self.c.post(
            "/einrichtung", data=form, files=files, headers={"Origin": PUBLIC_ORIGIN}
        )
        return r


@pytest.fixture
def setup_env(session_factory, public_app_factory) -> tuple[Setup, Any]:
    with session_factory() as s:
        code = instance.ensure_setup_code(s)
        s.commit()
    assert code is not None
    return Setup(public_app_factory(), code), session_factory


def test_code_only_hash_and_single_generation(session_factory) -> None:
    with session_factory() as s:
        code = instance.ensure_setup_code(s)
        s.commit()
        assert code
        assert re.fullmatch(r"[0-9A-Z]{5}(-[0-9A-Z]{5}){3}", code)
        row = s.get(InstanceSettings, 1)
        assert row is not None
        assert row.setup_code_hash
        assert code not in row.setup_code_hash
        assert instance.ensure_setup_code(s) is None  # nur einmal
        assert instance.check_code(s, code.lower().replace("-", " "))
        rotated = instance.ensure_setup_code(s, rotate=True)
        s.commit()
        assert rotated
        assert not instance.check_code(s, code)


def test_no_code_when_admin_exists(session_factory) -> None:
    bootstrap(session_factory)
    with session_factory() as s:
        assert not instance.setup_open(s)
        assert instance.ensure_setup_code(s) is None


def test_get_and_wrong_code(setup_env) -> None:
    st, _sf = setup_env
    r = st.c.get("/einrichtung")
    assert r.status_code == 200
    assert r.headers["referrer-policy"] == "same-origin"
    assert r.headers["cache-control"] == "no-store"
    assert 'name="code"' in r.text
    bad = Setup(st.c, "AAAAA-AAAAA-AAAAA-AAAAA").post("anmelden")
    assert bad.status_code == 403
    assert "stimmt nicht" in bad.text
    assert "AAAAA" not in bad.text


def test_wrong_code_rate_limited(setup_env, monkeypatch) -> None:
    monkeypatch.setattr(ratelimit, "clock", lambda: 2_000_000_001.0)
    st, _sf = setup_env
    wrong = Setup(st.c, "x")
    codes = [wrong.post("anmelden").status_code for _ in range(11)]
    assert codes[:10] == [403] * 10
    assert codes[10] == 429
    # Gesperrt heisst gesperrt: auch der richtige Code wird im Fenster nicht ausgewertet
    locked = st.post("anmelden")
    assert locked.status_code == 429
    assert "retry-after" in locked.headers


def test_origin_required(setup_env) -> None:
    st, _sf = setup_env
    for headers in ({}, {"Origin": "null"}, {"Origin": "https://evil.example"}):
        r = st.c.post(
            "/einrichtung",
            data={"code": st.code, "aktion": "seitenname", "site_name": "X"},
            headers=headers,
        )
        assert r.status_code == 403
        assert "PUBLIC_BASE_URL" in r.text or PUBLIC_ORIGIN in r.text


def test_full_flow(setup_env, certs: CertFactory, admin_app_factory) -> None:
    st, sf = setup_env
    assert st.post("anmelden").status_code == 200
    # Abschluss ohne Pflichtangaben scheitert
    assert st.post("abschliessen").status_code == 422
    assert st.post("seitenname", site_name="  Patch-Lage   ACME ").status_code == 200
    # Admin vor der CA -> Hinweis
    r = st.post("admin_zertifikat", files={"admin_datei": ("a.crt", certs.issue().pem)})
    assert r.status_code == 422
    assert "zuerst das CA-Bundle" in r.text
    r = st.post("ca", files={"ca_datei": ("ca.pem", pem(certs.ca_cert))})
    assert r.status_code == 200, r.text
    assert "CA-Bundle geprüft" in r.text
    # Fremdes Zertifikat wird abgewiesen
    foreign = CertFactory("Fremde CA").issue()
    r = st.post("admin_zertifikat", files={"admin_datei": ("f.crt", foreign.pem)})
    assert r.status_code == 422
    assert "nicht von einer CA" in r.text
    admin_cert = certs.issue(cn="Erste Admin")
    r = st.post(
        "admin_zertifikat",
        files={"admin_datei": ("a.crt", admin_cert.pem)},
        display_name="Erste Admin",
    )
    assert r.status_code == 200, r.text
    crl = make_crl((certs.ca_cert, certs.ca_key), revoked=12345)
    assert st.post("crl", files={"crl_datei": ("c.pem", crl)}).status_code == 200
    done = st.post("abschliessen")
    assert done.status_code == 200
    assert 'data-testid="setup-done"' in done.text

    with sf() as s:
        row = s.get(InstanceSettings, 1)
        assert row is not None
        assert row.site_name == "Patch-Lage ACME"
        assert row.setup_completed_at is not None
        assert row.setup_code_hash is None
        assert row.setup_draft is None
        assert row.admin_ca_pem
        assert row.admin_crl_pem
        assert row.trust_version == 1
        adm = s.execute(select(AdminUser)).scalar_one()
        assert adm.issuer_norm != "*"  # an den Aussteller gebunden
        assert adm.display_name == "Erste Admin"
        actions = {a.action for a in s.execute(select(AdminAuditLog)).scalars()}
        assert {"admin_hinzugefuegt", "einrichtung_abgeschlossen"} <= actions

    # Danach endgueltig geschlossen
    assert st.c.get("/einrichtung").status_code == 404
    assert st.post("anmelden").status_code == 404
    # Der neue Admin kommt in den Admin-Bereich, Seitenname erscheint
    admin = AdminClient(admin_app_factory(), admin_cert)
    page = admin.get("/admin/")
    assert page.status_code == 200
    assert "Patch-Lage ACME" in page.text


def test_admin_manual_and_external_ca(setup_env, certs: CertFactory) -> None:
    st, sf = setup_env
    assert st.post("ca_extern").status_code == 200
    r = st.post("admin_manuell", identifier=ADMIN_DN, issuer="", display_name="")
    assert r.status_code == 422
    assert "Aussteller-DN" in r.text
    r = st.post("admin_manuell", identifier=ADMIN_DN, issuer="", display_name="", issuer_any="ja")
    assert r.status_code == 200, r.text
    assert st.post("abschliessen").status_code == 200
    with sf() as s:
        row = s.get(InstanceSettings, 1)
        assert row is not None
        assert row.admin_ca_pem is None
        assert row.trust_version == 0
        assert s.execute(select(AdminUser.issuer_norm)).scalar_one() == "*"


@pytest.mark.parametrize(
    ("content", "text"),
    [
        (KEY_BEGIN + b"EC PRIVATE KEY-----\nAAAA\n", "privaten Schlüssel"),
        (b"hallo", "kein Zertifikat"),
    ],
)
def test_upload_errors_are_explained(setup_env, content: bytes, text: str) -> None:
    st, _sf = setup_env
    r = st.post("ca", files={"ca_datei": ("x.pem", content)})
    assert r.status_code == 422
    assert text in r.text


def test_oversized_body_rejected(setup_env) -> None:
    st, _sf = setup_env
    r = st.post("ca", files={"ca_datei": ("x.pem", b"a" * (1100 * 1024))})
    assert r.status_code == 413


def test_site_name_validation(setup_env) -> None:
    st, _sf = setup_env
    assert st.post("seitenname", site_name="   ").status_code == 422
    assert st.post("seitenname", site_name="x" * 81).status_code == 422
    r = st.post("seitenname", site_name="<script>alert(1)</script>")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text  # autoescape


def test_finish_race_second_finish_fails(setup_env) -> None:
    st, sf = setup_env
    st.post("ca_extern")
    st.post("admin_manuell", identifier=ADMIN_DN, issuer="", display_name="", issuer_any="ja")
    with sf() as s:
        instance.finish(s, "subject_dn")
        s.commit()
    with sf() as s, pytest.raises(instance.SetupError, match="bereits_eingerichtet"):
        instance.finish(s, "subject_dn")


# ================================================================ Admin-Einstellungen
@pytest.fixture
def admin(session_factory, admin_app_factory, certs: CertFactory) -> AdminClient:
    bootstrap(session_factory)
    return AdminClient(admin_app_factory(), certs.issue())


def test_settings_site_name(admin: AdminClient, session_factory) -> None:
    assert admin.get("/admin/einstellungen").status_code == 200
    r = admin.post("/admin/einstellungen/seitenname", {"site_name": "Lagebild"})
    assert r.status_code == 303
    page = admin.get("/admin/einstellungen")
    assert "<title>Einstellungen – Lagebild</title>" in page.text
    with session_factory() as s:
        log = s.execute(
            select(AdminAuditLog).where(AdminAuditLog.action == "seitenname_geaendert")
        ).scalar_one()
        assert log.details == {"vorher": "Notfall-Patch-Tracker"}


def test_settings_ca_lockout_guard(admin: AdminClient, certs: CertFactory, session_factory) -> None:
    other = make_ca("Andere Root")
    files = {"datei": ("ca.pem", pem(other[0]))}
    r = admin.post("/admin/einstellungen/ca", files=files)
    assert r.status_code == 422
    assert "nicht abgedeckt" in r.text
    r = admin.post("/admin/einstellungen/ca", {"bestaetigung": "ja"}, files=files)
    assert r.status_code == 303
    # Eigene CA: ohne Bestaetigung
    r = admin.post("/admin/einstellungen/ca", files={"datei": ("ca.pem", pem(certs.ca_cert))})
    assert r.status_code == 303
    page = admin.get("/admin/einstellungen")
    assert "Ihr Admin-Zertifikat wird von dieser CA abgedeckt" in page.text
    crl = make_crl((certs.ca_cert, certs.ca_key))
    r = admin.post("/admin/einstellungen/crl", files={"datei": ("c.crl", crl)})
    assert r.status_code == 303
    # CA entfernen, solange eine CRL haengt: nicht still verwerfen
    r = admin.post("/admin/einstellungen/ca/entfernen", {"bestaetigung": "ja"})
    assert r.status_code == 422
    assert "Sperrliste" in r.text
    assert admin.post("/admin/einstellungen/crl/entfernen").status_code == 303
    assert admin.post("/admin/einstellungen/ca/entfernen").status_code == 422
    r = admin.post("/admin/einstellungen/ca/entfernen", {"bestaetigung": "ja"})
    assert r.status_code == 303
    with session_factory() as s:
        row = s.get(InstanceSettings, 1)
        assert row is not None
        assert row.admin_ca_pem is None
        assert row.admin_crl_pem is None
        assert row.trust_version == 5
        actions = [a.action for a in s.execute(select(AdminAuditLog)).scalars()]
        assert actions.count("admin_ca_ersetzt") == 2
        assert "admin_ca_entfernt" in actions
        assert "admin_crl_entfernt" in actions


def test_settings_crl_revoking_own_cert_needs_confirmation(
    admin: AdminClient, certs: CertFactory
) -> None:
    r = admin.post("/admin/einstellungen/ca", files={"datei": ("ca.pem", pem(certs.ca_cert))})
    assert r.status_code == 303
    own_serial = admin.cert.cert.serial_number
    crl = make_crl((certs.ca_cert, certs.ca_key), revoked=own_serial)
    r = admin.post("/admin/einstellungen/crl", files={"datei": ("c.crl", crl)})
    assert r.status_code == 422
    assert "eigenes Admin-Zertifikat" in r.text
    r = admin.post(
        "/admin/einstellungen/crl", {"bestaetigung": "ja"}, files={"datei": ("c.crl", crl)}
    )
    assert r.status_code == 303


def test_crl_must_cover_every_ca(setup_env) -> None:
    st, sf = setup_env
    root = make_ca("Root")
    inter = make_ca("Inter", issuer=root)
    bundle = pem(inter[0]) + pem(root[0])
    assert st.post("ca", files={"ca_datei": ("b.pem", bundle)}).status_code == 200
    r = st.post("crl", files={"crl_datei": ("c.pem", make_crl(inter))})
    assert r.status_code == 422
    assert "jede CA des Bundles" in r.text
    both = make_crl(inter) + make_crl(root)
    assert st.post("crl", files={"crl_datei": ("c.pem", both)}).status_code == 200
    with sf() as s:
        stored = instance.draft(instance.get_row(s))["crl_pem"]
        assert stored.count("BEGIN X509 CRL") == 2


def test_chain_depth_and_revoked_admin(session_factory) -> None:
    root = make_ca("Root")
    inter = make_ca("Inter", issuer=root)
    leaf = make_ca("Admin Tief", issuer=inter, ca=False)[0]
    bundle = [inter[0], root[0]]
    with pytest.raises(instance.SetupError, match="kette_zu_tief"):
        instance.admin_from_cert(leaf, "subject_dn", bundle, max_depth=1)
    assert instance.admin_from_cert(leaf, "subject_dn", bundle, max_depth=2)["chain_depth"] == 2
    crls = pki.parse_crls(make_crl(inter, revoked=leaf.serial_number), bundle)
    with pytest.raises(instance.SetupError, match="zertifikat_gesperrt"):
        instance.admin_from_cert(leaf, "subject_dn", bundle, max_depth=2, crls=crls)


def test_trust_reset_and_close_if_admins(session_factory, certs: CertFactory) -> None:
    from npt.audit import Actor

    with session_factory() as s:
        row = instance.get_row(s)
        row.admin_ca_pem = pem(certs.ca_cert).decode()
        s.commit()
        assert instance.reset_trust(s, Actor.system("test")) is True
        s.commit()
        assert instance.get_row(s).admin_ca_pem is None
        assert instance.reset_trust(s, Actor.system("test")) is False
    bootstrap(session_factory)
    with session_factory() as s:
        assert instance.close_if_admins(s) is True
        s.commit()
        assert instance.get_row(s).setup_completed_at is not None


def test_site_name_rejects_bidi_controls() -> None:
    with pytest.raises(instance.SetupError, match="seitenname_ungueltig"):
        instance.clean_site_name("Patch\u202eLage")


def test_closed_installer_rejects_before_parsing(setup_env) -> None:
    st, sf = setup_env
    bootstrap(sf)
    r = st.post("ca", files={"ca_datei": ("x.pem", b"a" * 1000)})
    assert r.status_code == 404


def test_settings_requires_csrf(admin: AdminClient) -> None:
    r = admin.c.post(
        "/admin/einstellungen/seitenname", data={"site_name": "X"}, headers=admin.headers
    )
    assert r.status_code == 403


# ================================================================ Trust-Sync
def test_trust_sync_writes_and_removes(session_factory, tmp_path: Path, certs: CertFactory) -> None:
    from npt.jobs import trust

    settings = make_settings(TRUST_DIR=str(tmp_path), ADMIN_BASE_URL="https://gw.example:8443")
    with session_factory() as s:
        assert trust.sync(s, tmp_path, settings) is False  # nur Serverzertifikat
    key = tmp_path / "server.key"
    assert key.stat().st_mode & 0o777 == 0o640
    cert = x509.load_pem_x509_certificate((tmp_path / "server.crt").read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "gw.example" in san.get_values_for_type(x509.DNSName)
    with session_factory() as s:
        row = instance.get_row(s)
        assert row.gateway_cert_sha256 == pki.sha256_hex(cert)
        row.admin_ca_pem = pem(certs.ca_cert).decode()
        row.admin_crl_pem = make_crl((certs.ca_cert, certs.ca_key)).decode()
        s.commit()
        assert trust.sync(s, tmp_path, settings) is True
        assert trust.sync(s, tmp_path, settings) is False  # unveraendert
        assert (tmp_path / "ca-bundle.pem").read_text() == row.admin_ca_pem
        row = instance.get_row(s)
        row.admin_ca_pem = None
        s.commit()
        assert trust.sync(s, tmp_path, settings) is True
    assert not (tmp_path / "ca-bundle.pem").exists()
    assert not (tmp_path / "crl.pem").exists()
    assert cert == x509.load_pem_x509_certificate((tmp_path / "server.crt").read_bytes())


def test_public_app_has_no_admin_imports() -> None:
    import npt.web.public_setup as mod

    src = Path(mod.__file__).read_text()
    assert "npt.web.admin" not in src
