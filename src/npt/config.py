"""Konfiguration ausschliesslich ueber Umgebungsvariablen (Auftrag 2).

Kapazitaetsgrenzen stehen bewusst NICHT hier, sondern in ``npt.limits`` (NPT-09).
"""

from __future__ import annotations

import ipaddress
import json
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

IdKind = Literal["subject_dn", "san_email", "san_upn", "fingerprint_sha256"]
# Einzelrollen; APP_ROLE ist eine Komma-Liste daraus oder "all" (NPT-12 K1).
APP_ROLES = ("public", "admin", "jobs")
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")


def _split_list(value: str) -> list[str]:
    """Kommagetrennte Liste (fuer IPs/CIDRs)."""
    return [p.strip() for p in value.split(",") if p.strip()]


def parse_bootstrap_ids(raw: str) -> list[str]:
    """ADMIN_BOOTSTRAP_IDS: JSON-Liste oder durch ';'/Zeilenumbruch getrennt.

    Komma ist kein Trenner, weil DNs Kommas enthalten.
    """
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("["):
        data = json.loads(raw)
        if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
            raise ValueError("ADMIN_BOOTSTRAP_IDS: JSON muss eine Liste von Strings sein")
        return [x.strip() for x in data if x.strip()]
    parts = raw.replace("\n", ";").split(";")
    return [p.strip() for p in parts if p.strip()]


def parse_roles(raw: str) -> frozenset[str]:
    """APP_ROLE: ``all`` (exklusiv) oder Komma-Liste aus public/admin/jobs."""
    parts = [p.strip().lower() for p in raw.split(",")]
    if any(not p for p in parts):
        raise ValueError(f"APP_ROLE: leerer Eintrag in {raw!r}")
    if "all" in parts:
        if len(parts) > 1:
            raise ValueError("APP_ROLE: 'all' ist exklusiv und nicht kombinierbar")
        return frozenset(APP_ROLES)
    unknown = sorted(set(parts) - set(APP_ROLES))
    if unknown:
        raise ValueError(f"APP_ROLE: unbekannte Rolle(n) {unknown}, erlaubt: all oder {APP_ROLES}")
    return frozenset(parts)


def normalize_database_url(url: str) -> str:
    """postgres:// bzw. postgresql:// (z. B. Operator-Secrets) -> Treiber psycopg 3.

    Ohne Treiberangabe wuerde SQLAlchemy psycopg2 waehlen (nicht installiert).
    """
    try:
        parsed = make_url(url)
    except Exception:  # Text der Ausnahme kann das Passwort enthalten
        raise ValueError("DATABASE_URL ist keine gueltige Datenbank-URL") from None
    if parsed.drivername in ("postgres", "postgresql"):
        parsed = parsed.set(drivername="postgresql+psycopg")
    return parsed.render_as_string(hide_password=False)


def normalize_origin(url: str) -> str:
    """scheme://host[:port] in Kleinbuchstaben, Standardports entfernt."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"ungueltige Basis-URL: {url!r}")
    host = parts.hostname.lower()
    if ":" in host:  # IPv6
        host = f"[{host}]"
    port = parts.port
    default = 443 if parts.scheme == "https" else 80
    if port is None or port == default:
        return f"{parts.scheme}://{host}"
    return f"{parts.scheme}://{host}:{port}"


class Settings(BaseSettings):
    # hide_input_in_errors: Validierungsfehler nennen keine Werte (DATABASE_URL, Secrets).
    model_config = SettingsConfigDict(
        env_file=None, extra="ignore", case_sensitive=True, hide_input_in_errors=True
    )

    # --- Prozessmodell (NPT-02) ---
    APP_ROLE: str = "all"
    WEB_WORKERS: int = Field(default=6, ge=1, le=64)
    ADMIN_WORKERS: int = Field(default=2, ge=1, le=8)
    PUBLIC_LISTEN_PORT: int = 8000
    ADMIN_LISTEN_PORT: int = 8001
    # Admin-Port bindet nur an die admin_net-IP der App (sicherer Default: loopback).
    ADMIN_BIND_ADDR: str = "127.0.0.1"
    # Nur von hier werden X-SSL-Client-*-Header akzeptiert (direkte Absender-IP).
    ADMIN_GATEWAY_CIDR: str = "127.0.0.1/32"
    # Reverse Proxies, deren X-Forwarded-For fuer die Client-IP ausgewertet wird.
    TRUSTED_PROXY_IPS: str = ""

    # --- Datenbank ---
    DATABASE_URL: str = ""
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "npt"
    POSTGRES_USER: str = "npt"
    POSTGRES_PASSWORD: SecretStr = SecretStr("")
    # TLS zur Datenbank (libpq-Parameter, NPT-12 K6); leer = libpq-Default. Gelten fuer
    # POSTGRES_* und DATABASE_URL (ueberschreiben gleichnamige Query-Parameter der URL).
    POSTGRES_SSLMODE: str = ""
    POSTGRES_SSLROOTCERT: str = ""
    POSTGRES_SSLCERT: str = ""
    POSTGRES_SSLKEY: str = ""

    # --- URLs ---
    PUBLIC_BASE_URL: str = "http://localhost:8080"
    ADMIN_BASE_URL: str = ""

    # --- Sicherheit ---
    CSRF_SECRET: SecretStr = SecretStr("")
    ADMIN_CERT_ID_ATTRIBUTE: IdKind = "subject_dn"
    ADMIN_BOOTSTRAP_IDS: str = ""

    # --- Betrieb ---
    LOG_LEVEL: str = "INFO"
    DISPLAY_TZ: str = "Europe/Berlin"
    BACKUP_DIR: str = "/data/backups"

    # --- Rate-Limits (Auftrag 5.6/8.2: Defaults, ab Phase 3 genutzt) ---
    RATE_LIMIT_WRITES: int = Field(default=30, ge=1)
    RATE_LIMIT_WRITES_WINDOW_S: int = Field(default=10, ge=1)
    RATE_LIMIT_CREATE: int = Field(default=10, ge=1)
    RATE_LIMIT_CREATE_WINDOW_S: int = Field(default=60, ge=1)
    UPLOAD_MAX_MB: int = Field(default=20, ge=1, le=200)

    # --- SMTP / Mailversand (Auftrag 6.3, NPT-05) ---
    # Leerer SMTP_HOST = Versand deaktiviert (Kampagnen lassen sich anlegen, nicht senden).
    SMTP_HOST: str = ""
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    # starttls = Klartext-Verbindung + STARTTLS (Pflicht), tls = implizites TLS (465),
    # none = ohne Verschluesselung (nur interne Relays / Test-SMTP).
    SMTP_SECURITY: Literal["starttls", "tls", "none"] = "starttls"
    SMTP_USER: str = ""
    SMTP_PASSWORD: SecretStr = SecretStr("")
    # Absender (Pflicht fuer den Versand), z. B. "Notfall-Patch-Tracker <npt@firma.de>".
    SMTP_FROM: str = ""
    SMTP_REPLY_TO: str = ""
    SMTP_TIMEOUT_S: float = Field(default=30.0, gt=0, le=300)
    MAIL_RATE_PER_SEC: float = Field(default=5.0, gt=0, le=100)

    # --- Stammdaten-API (Stub, Phase 2) ---
    MASTER_API_ENABLED: bool = False
    MASTER_API_URL: str = ""
    MASTER_API_TOKEN: SecretStr = SecretStr("")
    MASTER_API_SCHEDULE_MINUTES: int = Field(default=0, ge=0)

    # --- Kubernetes (NPT-12) ---
    # Jobs-Prozess beruehrt diese Datei je Tick (leer = aus); ``python -m npt.probe``
    # prueft ihr Alter (Liveness des backend-Pods).
    JOBS_HEARTBEAT_FILE: str = ""

    @field_validator("APP_ROLE")
    @classmethod
    def _check_role(cls, v: str) -> str:
        parse_roles(v)
        return v

    @field_validator("POSTGRES_SSLMODE")
    @classmethod
    def _check_sslmode(cls, v: str) -> str:
        if v and v not in SSL_MODES:
            raise ValueError(f"POSTGRES_SSLMODE: erlaubt sind {SSL_MODES}")
        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def _check_database_url(cls, v: str) -> str:
        return normalize_database_url(v) if v else v

    @field_validator("ADMIN_GATEWAY_CIDR", "TRUSTED_PROXY_IPS")
    @classmethod
    def _check_networks(cls, v: str) -> str:
        for item in _split_list(v):
            ipaddress.ip_network(item, strict=False)
        return v

    @field_validator("PUBLIC_BASE_URL")
    @classmethod
    def _check_public_url(cls, v: str) -> str:
        normalize_origin(v)
        return v.rstrip("/")

    @field_validator("ADMIN_BASE_URL")
    @classmethod
    def _check_admin_url(cls, v: str) -> str:
        if v:
            normalize_origin(v)
        return v.rstrip("/")

    @field_validator("ADMIN_BOOTSTRAP_IDS")
    @classmethod
    def _check_bootstrap(cls, v: str) -> str:
        parse_bootstrap_ids(v)
        return v

    # ------------------------------------------------------------------
    @property
    def gateway_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        return [ipaddress.ip_network(x, strict=False) for x in _split_list(self.ADMIN_GATEWAY_CIDR)]

    @property
    def trusted_proxy_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        return [ipaddress.ip_network(x, strict=False) for x in _split_list(self.TRUSTED_PROXY_IPS)]

    @property
    def roles(self) -> frozenset[str]:
        return parse_roles(self.APP_ROLE)

    @property
    def bootstrap_ids(self) -> list[str]:
        return parse_bootstrap_ids(self.ADMIN_BOOTSTRAP_IDS)

    @property
    def ssl_params(self) -> dict[str, str]:
        """Gesetzte POSTGRES_SSL*-Werte als libpq-Query-Parameter."""
        pairs = (
            ("sslmode", self.POSTGRES_SSLMODE),
            ("sslrootcert", self.POSTGRES_SSLROOTCERT),
            ("sslcert", self.POSTGRES_SSLCERT),
            ("sslkey", self.POSTGRES_SSLKEY),
        )
        return {k: v for k, v in pairs if v}

    @property
    def sqlalchemy_url(self) -> str:
        if self.DATABASE_URL:
            url = make_url(self.DATABASE_URL)
        else:
            # URL.create statt f-String: quote_plus machte aus Leerzeichen im Passwort
            # ein "+", das SQLAlchemy in der Userinfo nicht zurueckwandelt.
            url = URL.create(
                "postgresql+psycopg",
                username=self.POSTGRES_USER,
                password=self.POSTGRES_PASSWORD.get_secret_value(),
                host=self.POSTGRES_HOST,
                port=self.POSTGRES_PORT,
                database=self.POSTGRES_DB,
            )
        ssl = self.ssl_params
        if ssl:
            url = url.update_query_dict(ssl)
        return url.render_as_string(hide_password=False)

    def require_runtime_secrets(self) -> None:
        """Fail fast: ohne CSRF-Secret bzw. DB-Zugang startet die App nicht."""
        if len(self.CSRF_SECRET.get_secret_value()) < 32:
            raise RuntimeError("CSRF_SECRET fehlt oder ist kuerzer als 32 Zeichen")
        if not self.DATABASE_URL and not self.POSTGRES_PASSWORD.get_secret_value():
            raise RuntimeError("POSTGRES_PASSWORD bzw. DATABASE_URL fehlt")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
