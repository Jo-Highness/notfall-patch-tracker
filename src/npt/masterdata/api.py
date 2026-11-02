"""Stammdaten per API-Abruf – **deaktivierter Stub** (Auftrag 4, ARCHITECTURE A3).

Konfiguration ueber ``MASTER_API_ENABLED``, ``MASTER_API_URL``,
``MASTER_API_TOKEN`` und optional ``MASTER_API_SCHEDULE_MINUTES``. Fuer die
spaetere Anbindung ist nur :meth:`ApiMasterDataSource.map_response` zu
implementieren (Abbildung der API-Antwort auf ``MasterRecord``).

Verbindliche Auflage: Ein (auch zeitgesteuerter) API-Abruf legt nur eine
**Vorschau** an (``staged_uploads``); uebernommen wird ausschliesslich nach
Bestaetigung durch einen Admin – derselbe Diff-Mechanismus wie beim Excel-Import.

Weiterleitungen (K14): nur auf ``https://`` mit demselben Host und Port wie
``MASTER_API_URL``; jede andere Weiterleitung bricht den Abruf ab, bevor eine Anfrage an
das neue Ziel geht. Der Bearer-Token steht als nicht weitergeleiteter Header in der
Anfrage und wird nur bei einer zulaessigen Weiterleitung erneut gesetzt.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from http.client import HTTPMessage
from typing import IO, Any, Final

from npt.config import Settings
from npt.masterdata.source import (
    MasterDataError,
    MasterDataSource,
    MasterDataSourceDisabledError,
    MasterRecord,
)

log = logging.getLogger("npt.masterdata.api")

FETCH_TIMEOUT_S: Final = 30
MAX_RESPONSE_BYTES: Final = 50 * 1024 * 1024


def _origin(url: str) -> tuple[str, str, int] | None:
    """(Schema, Host, Port) einer https-URL, sonst ``None``."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port or 443
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not parts.hostname:
        return None
    return ("https", parts.hostname.lower(), port)


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Folgt Weiterleitungen nur auf https mit gleichem Host/Port; setzt dann den Token neu."""

    def __init__(self, url: str, token: str) -> None:
        super().__init__()
        self._origin = _origin(url)
        self._token = token

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if self._origin is None or _origin(newurl) != self._origin:
            fp.close()
            # Ziel-URL bewusst nicht in der Meldung (koennte Zugangsdaten enthalten).
            raise MasterDataError(
                f"Stammdaten-API leitet weiter (HTTP {code}), aber nicht auf https:// "
                "mit demselben Host und Port; Abruf abgebrochen. MASTER_API_URL auf die "
                "endgueltige Adresse setzen."
            )
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and self._token:
            new.add_unredirected_header("Authorization", f"Bearer {self._token}")
        return new


def open_api(url: str, token: str, timeout: float = FETCH_TIMEOUT_S) -> Any:
    """GET mit Token und eingeschraenkten Weiterleitungen (siehe Moduldoku)."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    if token:
        # Nicht weitergeleiteter Header: urllib uebernimmt ihn bei Weiterleitungen nicht.
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    opener = urllib.request.build_opener(SameOriginRedirectHandler(url, token))
    return opener.open(req, timeout=timeout)


class ApiMasterDataSource(MasterDataSource):
    source_kind = "api"

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self._settings.MASTER_API_ENABLED and self._settings.MASTER_API_URL)

    def fetch(self) -> list[MasterRecord]:
        if not self.enabled:
            raise MasterDataSourceDisabledError("Der Stammdaten-API-Abruf ist nicht aktiviert.")
        url = self._settings.MASTER_API_URL
        if not url.startswith("https://"):
            raise MasterDataError("MASTER_API_URL muss https:// verwenden.")
        token = self._settings.MASTER_API_TOKEN.get_secret_value()
        with open_api(url, token) as resp:
            body = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise MasterDataError("Antwort der Stammdaten-API ist zu groß.")
        return self.map_response(json.loads(body))

    def map_response(self, payload: Any) -> list[MasterRecord]:
        """Abbildung der API-Antwort auf Stammdatensaetze (noch nicht festgelegt).

        Umsetzung bei Anbindung: ``self.columns`` auf die gelieferten Felder setzen
        und je System einen ``MasterRecord(system_id, values)`` liefern.
        """
        raise NotImplementedError("Abbildung der Stammdaten-API-Antwort ist nicht implementiert")


class ApiPullSchedule:
    """Optionaler Zeitplan fuer den Jobs-Prozess: legt nur eine Vorschau an."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._last = 0.0

    def due(self, now: float | None = None) -> bool:
        minutes = self._settings.MASTER_API_SCHEDULE_MINUTES
        if not self._settings.MASTER_API_ENABLED or minutes <= 0:
            return False
        current = now if now is not None else time.monotonic()
        if self._last and current - self._last < minutes * 60:
            return False
        self._last = current
        return True
