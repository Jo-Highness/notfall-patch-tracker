"""API-Stub der Stammdaten (deaktiviert, nur Vorschau) – Auftrag 4, ARCHITECTURE A3."""

from __future__ import annotations

import pytest

from npt.jobs import master_api
from npt.masterdata.api import ApiMasterDataSource, ApiPullSchedule
from npt.masterdata.source import MasterDataSource, MasterDataSourceDisabledError
from tests.conftest import make_settings


def test_api_source_is_disabled_by_default() -> None:
    src = ApiMasterDataSource(make_settings())
    assert isinstance(src, MasterDataSource)
    assert not src.enabled
    with pytest.raises(MasterDataSourceDisabledError):
        src.fetch()


def test_api_mapping_is_the_only_missing_piece() -> None:
    src = ApiMasterDataSource(make_settings(MASTER_API_ENABLED=True, MASTER_API_URL="https://x"))
    with pytest.raises(NotImplementedError):
        src.map_response({"items": []})


def test_schedule_and_job_registration() -> None:
    off = make_settings()
    assert master_api.make_job(off, lambda: None) is None  # type: ignore[arg-type,return-value]
    on = make_settings(
        MASTER_API_ENABLED=True, MASTER_API_URL="https://x", MASTER_API_SCHEDULE_MINUTES=5
    )
    assert master_api.make_job(on, lambda: None) is not None  # type: ignore[arg-type,return-value]
    sched = ApiPullSchedule(on)
    assert sched.due(now=1000.0)
    assert not sched.due(now=1100.0)
    assert sched.due(now=1000.0 + 301)
