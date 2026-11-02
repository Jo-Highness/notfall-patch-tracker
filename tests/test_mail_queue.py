"""Kampagnen-Laeufe und Mail-Queue mit Sender (NPT-05): Drosselung, Retry, Status unklar,
Doppelversand-Schutz, genau ein aktiver Sender, echter SMTP-Weg gegen einen lokalen Server."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from npt.admins import Actor
from npt.db.models import AdminAuditLog, MailDelivery, MailRun, ProjectSystem
from npt.mail import campaigns, queue
from npt.mail.campaigns import CampaignInput
from npt.mail.transport import SmtpTransport
from tests.mail_helpers import FakeTransport, mail_config, perm_error, smtp_server, temp_error
from tests.project_helpers import make_project, seed_master

ACTOR = Actor("issuer", "cn=test admin", "192.0.2.10")
BODY = "<p>Hallo,</p><p>bitte prüfen:</p><p>[LINK]</p>"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def engine(session_factory: sessionmaker[Session]) -> Engine:
    bind = session_factory.kw["bind"]
    assert isinstance(bind, Engine)
    return bind


@pytest.fixture
def proj(session_factory: sessionmaker[Session]) -> Any:
    seed_master(session_factory, 10)  # owner0..owner4@firma.test, je 2 Systeme
    return make_project(session_factory)


def new_campaign(sf: sessionmaker[Session], pid: int, selector: str = "betroffen_leer") -> int:
    with sf() as s:
        c = campaigns.create_campaign(s, ACTOR, pid, CampaignInput("Notfall", BODY, selector))
        s.commit()
        return c.id


def start(sf: sessionmaker[Session], pid: int, cid: int, **kw: Any) -> MailRun:
    with sf() as s:
        c = campaigns.get_campaign(s, pid, cid)
        if "expected_count" not in kw:
            mode = kw.get("mode", "alle")
            kw["expected_count"] = len(campaigns.plan_run(s, c, mode).recipients)
        kw.setdefault("mode", "alle")
        kw.setdefault("expected_run", c.send_run)
        run = campaigns.start_run(s, ACTOR, pid, cid, **kw)
        s.commit()
        return run


def statuses(sf: sessionmaker[Session], cid: int, run: int | None = None) -> dict[str, str]:
    with sf() as s:
        stmt = select(MailDelivery.email_norm, MailDelivery.status).where(
            MailDelivery.campaign_id == cid
        )
        if run is not None:
            stmt = stmt.where(MailDelivery.send_run == run)
        return dict(s.execute(stmt).all())


_SENDERS: list[queue.MailSender] = []


@pytest.fixture(autouse=True)
def _release_senders() -> Any:
    yield
    while _SENDERS:
        _SENDERS.pop().release_lock()


def sender(
    engine: Engine, transport: FakeTransport, clock: FakeClock | None = None, **cfg: Any
) -> queue.MailSender:
    clock = clock or FakeClock()
    s = queue.MailSender(
        engine,
        mail_config(**cfg),
        base_url="http://npt.test",
        transport_factory=transport.factory,
        clock=clock,
        sleep=clock.sleep,
    )
    assert s.acquire_lock()
    _SENDERS.append(s)
    return s


def due_now(sf: sessionmaker[Session]) -> None:
    with sf() as s:
        s.execute(update(MailDelivery).values(next_attempt_at=func.now()))
        s.commit()


# ---------------------------------------------------------------- Laeufe
def test_start_freezes_recipients_and_content(session_factory, proj, engine) -> None:
    cid = new_campaign(session_factory, proj.id)
    run = start(session_factory, proj.id, cid)
    assert (run.send_run, run.recipient_count, run.system_count) == (1, 5, 10)
    with session_factory() as s:
        rows = s.scalars(select(MailDelivery).order_by(MailDelivery.email_norm)).all()
        assert [d.email_norm for d in rows] == [f"owner{i}@firma.test" for i in range(5)]
        assert all(d.status == "ausstehend" for d in rows)
        assert rows[1].systems == [
            {"id": "APP-00001", "name": "System 1"},
            {"id": "APP-00006", "name": "System 6"},
        ]
        # Aenderungen nach dem Start wirken nicht auf den eingefrorenen Lauf
        s.execute(
            update(ProjectSystem)
            .where(ProjectSystem.system_id == "APP-00001")
            .values(system_name="Umbenannt", affected="ja")
        )
        c = campaigns.get_campaign(s, proj.id, cid)
        c.body_html = "<p>neuer Text [LINK]</p>"
        s.commit()
        audit = s.scalars(
            select(AdminAuditLog).where(AdminAuditLog.action == "mail_versand_gestartet")
        ).one()
        assert audit.details
        assert audit.details["empfaenger"] == 5
    t = FakeTransport()
    assert sender(engine, t).drain() == 5
    by_to = dict(t.sent)
    html = by_to["owner1@firma.test"].get_body(("html",)).get_content()  # type: ignore[union-attr]
    assert "System 1 (APP-00001)" in html
    assert "bitte prüfen" in html
    assert "neuer Text" not in html


def test_double_start_and_rerun_only_explicit(session_factory, proj) -> None:
    cid = new_campaign(session_factory, proj.id)
    start(session_factory, proj.id, cid, expected_run=0)
    # doppelt abgeschicktes Formular (gleiche expected_run) -> kein zweiter Lauf
    with pytest.raises(campaigns.CampaignError) as exc:
        start(session_factory, proj.id, cid, expected_run=0)
    assert exc.value.code == "mail_bereits_gestartet"
    # neuer Lauf, solange der erste noch offen ist -> abgelehnt
    with pytest.raises(campaigns.CampaignError) as exc:
        start(session_factory, proj.id, cid, expected_run=1)
    assert exc.value.code == "mail_versand_laeuft"
    with session_factory() as s:
        s.execute(update(MailDelivery).values(status="gesendet"))
        s.commit()
    run2 = start(session_factory, proj.id, cid, expected_run=1)
    assert run2.send_run == 2
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(MailDelivery)) == 10
        counts = dict(
            s.execute(
                select(MailDelivery.send_run, func.count()).group_by(MailDelivery.send_run)
            ).all()
        )
    assert counts == {1: 5, 2: 5}


def test_unique_campaign_run_email(session_factory, proj) -> None:
    cid = new_campaign(session_factory, proj.id)
    start(session_factory, proj.id, cid)
    with session_factory() as s:
        s.add(MailDelivery(campaign_id=cid, send_run=1, email_norm="owner0@firma.test"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_audience_changed_requires_new_confirmation(session_factory, proj) -> None:
    cid = new_campaign(session_factory, proj.id)
    with session_factory() as s:
        s.execute(
            update(ProjectSystem)
            .where(ProjectSystem.system_id.in_(["APP-00004", "APP-00009"]))
            .values(affected="nein")
        )  # owner4 hat danach kein leeres System mehr
        s.commit()
    with pytest.raises(campaigns.AudienceChangedError) as exc:
        start(session_factory, proj.id, cid, expected_count=5)
    assert exc.value.count == 4
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(MailRun)) == 0
    assert start(session_factory, proj.id, cid, expected_count=4).recipient_count == 4


def test_no_recipients_and_readonly_project(session_factory, proj) -> None:
    cid = new_campaign(session_factory, proj.id, "ja_ohne_aktion")
    with pytest.raises(campaigns.CampaignError) as exc:
        start(session_factory, proj.id, cid, expected_count=0)
    assert exc.value.code == "mail_keine_empfaenger"
    with session_factory() as s:
        s.execute(text("UPDATE projects SET status = 'abgeschlossen'"))
        s.commit()
    cid2 = new_campaign(session_factory, proj.id)
    with pytest.raises(campaigns.CampaignError) as exc:
        start(session_factory, proj.id, cid2, expected_count=5)
    assert exc.value.code == "schreibgeschuetzt"


# ---------------------------------------------------------------- Sender
def test_throttle_token_bucket(session_factory, proj, engine) -> None:
    cid = new_campaign(session_factory, proj.id)
    start(session_factory, proj.id, cid)
    clock = FakeClock()
    t = FakeTransport()
    s = sender(engine, t, clock, MAIL_RATE_PER_SEC=2.0)
    t0 = clock.now
    assert s.drain() == 5
    # Kapazitaet 1: erste Mail sofort, danach je 1/Rate = 0,5 s
    assert clock.now - t0 == pytest.approx(2.0)
    assert all(x == pytest.approx(0.5) for x in clock.slept)
    assert statuses(session_factory, cid) == {f"owner{i}@firma.test": "gesendet" for i in range(5)}


def test_token_bucket_unit() -> None:
    clock = FakeClock()
    bucket = queue.TokenBucket(5.0, clock=clock, sleep=clock.sleep)
    start_t = clock.now
    for _ in range(11):
        bucket.acquire()
    assert clock.now - start_t == pytest.approx(2.0)  # 10 Abstaende zu 0,2 s
    clock.now += 10  # lange Pause: hoechstens eine Mail ohne Warten (kein Burst)
    bucket.acquire()
    before = clock.now
    bucket.acquire()
    assert clock.now - before == pytest.approx(0.2)


def test_retry_on_4xx_then_success(session_factory, proj, engine) -> None:
    cid = new_campaign(session_factory, proj.id)
    start(session_factory, proj.id, cid)
    fails = {"owner2@firma.test": 2}

    def script(to: str, _n: int) -> BaseException | None:
        if fails.get(to, 0) > 0:
            fails[to] -= 1
            return temp_error(451)
        return None

    t = FakeTransport(script=script)
    s = sender(engine, t)
    assert s.drain() == 5
    with session_factory() as db:
        d = db.scalars(
            select(MailDelivery).where(MailDelivery.email_norm == "owner2@firma.test")
        ).one()
        assert (d.status, d.attempts) == ("ausstehend", 1)
        assert d.last_error
        assert "451" in d.last_error
        assert d.last_error.startswith("Versuch 1")
        wait = db.scalar(
            select(func.extract("epoch", MailDelivery.next_attempt_at - func.now())).where(
                MailDelivery.id == d.id
            )
        )
        assert 25 < float(wait) <= queue.BACKOFF_S[0]
    assert s.drain() == 0  # noch nicht faellig (Backoff)
    due_now(session_factory)
    assert s.drain() == 1
    due_now(session_factory)
    assert s.drain() == 1
    with session_factory() as db:
        d = db.scalars(
            select(MailDelivery).where(MailDelivery.email_norm == "owner2@firma.test")
        ).one()
        assert (d.status, d.attempts, d.last_error) == ("gesendet", 3, None)
        assert d.message_id
        assert d.sent_at
    assert [to for to, _ in t.sent].count("owner2@firma.test") == 1


def test_temporary_errors_give_up_after_5_attempts(session_factory, proj, engine) -> None:
    cid = new_campaign(session_factory, proj.id)
    start(session_factory, proj.id, cid)
    t = FakeTransport(script=lambda to, _n: TimeoutError() if to == "owner0@firma.test" else None)
    s = sender(engine, t)
    s.drain()
    for _ in range(queue.MAX_ATTEMPTS - 1):
        due_now(session_factory)
        assert s.drain() == 1
    due_now(session_factory)
    assert s.drain() == 0
    with session_factory() as db:
        d = db.scalars(
            select(MailDelivery).where(MailDelivery.email_norm == "owner0@firma.test")
        ).one()
    assert (d.status, d.attempts) == ("fehlgeschlagen", 5)
    assert d.last_error
    assert d.last_error.startswith("Nach 5 Versuchen")
    assert [queue.backoff_for(n) for n in range(1, 6)] == [30.0, 60.0, 120.0, 240.0, 240.0]


def test_5xx_is_final(session_factory, proj, engine) -> None:
    cid = new_campaign(session_factory, proj.id)
    start(session_factory, proj.id, cid)
    t = FakeTransport(script=lambda to, _n: perm_error(550) if to == "owner3@firma.test" else None)
    snd = sender(engine, t)
    assert snd.drain() == 5
    st = statuses(session_factory, cid)
    assert st.pop("owner3@firma.test") == "fehlgeschlagen"
    assert set(st.values()) == {"gesendet"}
    with session_factory() as db:
        d = db.scalars(
            select(MailDelivery).where(MailDelivery.email_norm == "owner3@firma.test")
        ).one()
    assert d.attempts == 1
    assert d.last_error
    assert "550" in d.last_error
    due_now(session_factory)
    assert snd.drain() == 0  # nie erneut


def test_stale_in_versand_marked_unclear_not_resent(session_factory, proj, engine) -> None:
    cid = new_campaign(session_factory, proj.id)
    start(session_factory, proj.id, cid)
    with session_factory() as s:
        s.execute(
            update(MailDelivery)
            .where(MailDelivery.email_norm == "owner1@firma.test")
            .values(
                status="in_versand",
                sending_since=func.now() - text("interval '11 minutes'"),
                attempts=1,
            )
        )
        s.execute(
            update(MailDelivery)
            .where(MailDelivery.email_norm == "owner2@firma.test")
            .values(
                status="in_versand",
                sending_since=func.now() - text("interval '5 minutes'"),
                attempts=1,
            )
        )
        s.commit()
    t = FakeTransport()
    snd = sender(engine, t)
    assert snd.recover_stale() == 1
    assert snd.drain() == 3
    st = statuses(session_factory, cid)
    assert st["owner1@firma.test"] == "fehlgeschlagen"
    assert st["owner2@firma.test"] == "in_versand"  # juenger als 10 min: bleibt
    assert "owner1@firma.test" not in [to for to, _ in t.sent]
    with session_factory() as db:
        err = db.scalar(
            select(MailDelivery.last_error).where(MailDelivery.email_norm == "owner1@firma.test")
        )
    assert err
    assert err.startswith("Status unklar")


def test_only_one_active_sender(session_factory, proj, engine) -> None:
    a = queue.MailSender(engine, mail_config(), base_url="http://npt.test")
    b = queue.MailSender(engine, mail_config(), base_url="http://npt.test")
    results: list[bool] = []
    barrier = threading.Barrier(2)

    def grab(s: queue.MailSender) -> None:
        barrier.wait()
        results.append(s.acquire_lock())

    threads = [threading.Thread(target=grab, args=(s,)) for s in (a, b)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(results) == [False, True]
    active, passive = (a, b) if a.active else (b, a)
    assert not passive.run_once()  # ohne Sperre wird nichts abgeholt
    active.release_lock()
    assert passive.acquire_lock()
    passive.release_lock()


def test_two_running_senders_send_each_mail_once(session_factory, proj, engine) -> None:
    cid = new_campaign(session_factory, proj.id)
    start(session_factory, proj.id, cid)
    t1, t2 = FakeTransport(), FakeTransport()
    stop = threading.Event()
    senders = [
        queue.MailSender(
            engine,
            mail_config(MAIL_RATE_PER_SEC=50.0),
            base_url="http://npt.test",
            transport_factory=t.factory,
        )
        for t in (t1, t2)
    ]
    threads = [threading.Thread(target=s.run, args=(stop,)) for s in senders]
    for th in threads:
        th.start()
    try:
        for _ in range(100):
            if set(statuses(session_factory, cid).values()) == {"gesendet"}:
                break
            stop.wait(0.1)
    finally:
        stop.set()
        for th in threads:
            th.join(10)
    assert set(statuses(session_factory, cid).values()) == {"gesendet"}
    sent = [to for to, _ in t1.sent + t2.sent]
    assert sorted(sent) == [f"owner{i}@firma.test" for i in range(5)]
    assert not t1.sent or not t2.sent  # nur einer war aktiv


def test_claim_skip_locked(session_factory, proj, engine) -> None:
    cid = new_campaign(session_factory, proj.id)
    start(session_factory, proj.id, cid)
    with engine.connect() as c1, engine.connect() as c2:
        a = queue.claim_next(c1)
        b = queue.claim_next(c2)  # Zeile von c1 gesperrt -> naechste
        assert a
        assert b
        assert a.id != b.id
        c1.commit()
        c2.commit()
    st = statuses(session_factory, cid)
    assert list(st.values()).count("in_versand") == 2


def test_only_failed_rerun_and_cancel(session_factory, proj, engine) -> None:
    cid = new_campaign(session_factory, proj.id)
    start(session_factory, proj.id, cid)
    bad = {"owner0@firma.test", "owner4@firma.test"}
    sender(engine, FakeTransport(script=lambda to, _n: perm_error() if to in bad else None)).drain()
    with session_factory() as s:
        c = campaigns.get_campaign(s, proj.id, cid)
        assert {r.email for r in campaigns.failed_recipients(s, cid, 1)} == bad
        assert campaigns.plan_run(s, c, "nur_fehlgeschlagene").system_count == 4
    run2 = start(session_factory, proj.id, cid, mode="nur_fehlgeschlagene", expected_run=1)
    assert (run2.send_run, run2.recipient_count, run2.mode) == (2, 2, "nur_fehlgeschlagene")
    with session_factory() as s:
        n = campaigns.cancel_run(s, ACTOR, proj.id, cid)
        s.commit()
    assert n == 2
    assert statuses(session_factory, cid, 2) == dict.fromkeys(bad, "fehlgeschlagen")
    with session_factory() as s:
        st = campaigns.run_status(s, cid, 2)
        assert not st.active
        assert st.counts["fehlgeschlagen"] == 2
        assert st.run is not None
        assert st.run.cancelled_at is not None
        assert st.deliveries[0].last_error == queue.CANCELLED_TEXT


def test_deleted_project_cascades(session_factory, proj, engine) -> None:
    cid = new_campaign(session_factory, proj.id)
    start(session_factory, proj.id, cid)
    with session_factory() as s:
        s.execute(text("DELETE FROM projects"))
        s.commit()
        assert s.scalar(select(func.count()).select_from(MailDelivery)) == 0
    assert sender(engine, FakeTransport()).drain() == 0


# ---------------------------------------------------------------- echter SMTP-Weg (lokal)
def test_smtp_transport_against_local_server(session_factory, proj, engine) -> None:
    cid = new_campaign(session_factory, proj.id)
    start(session_factory, proj.id, cid)
    with smtp_server() as srv:
        srv.codes = {"owner1@firma.test": 451, "owner2@firma.test": 550}
        cfg = {"SMTP_PORT": srv.server_address[1]}
        snd = queue.MailSender(
            engine, mail_config(**cfg), base_url="http://npt.test", transport_factory=SmtpTransport
        )
        assert snd.acquire_lock()
        try:
            assert snd.drain() == 5
        finally:
            snd.release_lock()
        st = statuses(session_factory, cid)
        assert st == {
            "owner0@firma.test": "gesendet",
            "owner1@firma.test": "ausstehend",  # 451 -> Retry
            "owner2@firma.test": "fehlgeschlagen",  # 550 -> endgueltig
            "owner3@firma.test": "gesendet",
            "owner4@firma.test": "gesendet",
        }
        assert len(srv.messages) == 3
        assert all(len(rcpts) == 1 for rcpts, _ in srv.messages)
        for msg in srv.parsed():
            assert msg.get_content_type() == "multipart/alternative"
            assert msg["Subject"] == "Notfall"
            assert "[LINK]" not in msg.as_string()
            html = msg.get_body(("html",))
            assert html is not None
            assert "http://npt.test/p/" in html.get_content()
