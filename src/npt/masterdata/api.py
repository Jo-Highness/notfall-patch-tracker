"""Stammdaten per API-Abruf – **deaktivierter Stub** (Auftrag 4, ARCHITECTURE A3).

Konfiguration ueber ``MASTER_API_ENABLED``, ``MASTER_API_URL``,
``MASTER_API_TOKEN`` und optional ``MASTER_API_SCHEDULE_MINUTES``. Fuer die
spaetere Anbindung ist nur :meth:`ApiMasterDataSource.map_response` zu
implementieren (Abbildung der API-Antwort auf ``MasterRecord``).

Verbindliche Auflage: Ein (auch zeitgesteuerter) API-Abruf legt nur eine
**Vorschau** an (``staged_uploads``); uebernommen wird ausschliesslich nach
Bestaetigung durch einen Admin – derselbe Diff-Mechanismus wie beim Excel-Import.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any, Final

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
        req = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
        token = self._settings.MASTER_API_TOKEN.get_secret_value()
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:  # noqa: S310
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
