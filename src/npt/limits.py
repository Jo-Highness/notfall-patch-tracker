"""Zentrale Kapazitaets- und Zeitgrenzen (NPT-09).

Kapazitaetsgrenzen sind bewusst keine .env-Stellschrauben. Die Rangfolge der
Grenzen wird beim Start mit :func:`check_ranking` geprueft: die inneren
Grenzen (Semaphor, Pool) muessen greifen, bevor das Rueckfallnetz von Uvicorn
(limit_concurrency) abweist.
"""

from __future__ import annotations

# --- Datenbank-Pool je Worker-Prozess ---
DB_POOL_SIZE = 10
DB_MAX_OVERFLOW = 5
DB_POOL_TIMEOUT_S = 2.0
DB_LOCK_TIMEOUT_MS = 2000
DB_STATEMENT_TIMEOUT_MS = 30_000
DB_CONNECT_TIMEOUT_S = 5

# --- Zulassung (Admission) je Worker ---
# 300 statt 200 (Phase 9, J10): Gunicorn verteilt neue Verbindungen ungleich auf die
# Worker; im Lasttest mit 520 Streams auf 4 Workern liefen einzelne Worker an 200 an.
SSE_MAX_PER_WORKER = 300
REQUEST_SEMAPHORE = 15  # gleichzeitige DB-gebundene Requests (<= Pool + Overflow)
SEMAPHORE_WAIT_S = 2.0
UVICORN_LIMIT_CONCURRENCY = 500  # Rueckfallnetz, muss ueber allen inneren Grenzen liegen
UVICORN_KEEPALIVE_S = 75

# --- Retry-After je Abweisungsfall (Sekunden, DECISIONS D4) ---
BUSY_RETRY_AFTER_S = 1  # 503 gesperrt: Lock-/Statement-Timeout beim Schreiben
OVERLOADED_RETRY_AFTER_S = 2  # 503 ueberlastet: Semaphor oder DB-Pool erschoepft
DB_UNAVAILABLE_RETRY_AFTER_S = 5  # 503 datenbank: Datenbank nicht erreichbar
READY_RETRY_AFTER_S = 5  # 503 /readyz: letzte Probe zu alt

# --- Live-Updates (NPT-03) ---
SSE_HEARTBEAT_S = 15
SSE_QUEUE_MAX = 64  # Ereignisse je Client; Ueberlauf -> Verbindung schliessen
SSE_RETRY_MS = 3000  # Wiederverbindungs-Wartezeit des Browsers (SSE "retry")
SSE_RETRY_AFTER_S = 5  # Retry-After bei voller SSE-Grenze
LIVE_BUNDLE_S = 0.15  # Buendelfenster je Worker
LIVE_LISTEN_CHECK_S = 30.0  # Lebenszeichen-Abfrage auf der LISTEN-Verbindung
LIVE_RECONNECT_MAX_S = 10.0
# Uvicorn bricht offene Streams nach dieser Zeit ab (Clients verbinden neu);
# muss unter dem graceful_timeout von Gunicorn liegen.
SSE_SHUTDOWN_GRACE_S = 5
GUNICORN_TIMEOUT_S = 60  # Herzschlag Worker -> Arbiter, nicht Request-Dauer
GUNICORN_GRACEFUL_TIMEOUT_S = 20

# --- Gesundheit ---
HEALTH_PROBE_INTERVAL_S = 10.0
HEALTH_PROBE_TIMEOUT_S = 5.0
HEALTH_READY_MAX_AGE_S = 30.0
HEALTH_STALL_EXIT_S = 120.0

# --- Sicherheit ---
CSRF_TTL_S = 12 * 3600
CSRF_FUTURE_SKEW_S = 300
MAX_CERT_HEADER_BYTES = 16 * 1024

# --- Supervisor ---
SUPERVISOR_STOP_TIMEOUT_S = 25.0
MIGRATION_DB_WAIT_S = 60.0
# Wartezeit auf den Migrations-Lock (NPT-12 K2): mehrere Pods starten gleichzeitig,
# eine lange Migration darf die anderen nicht in einen Crash-Loop schicken.
MIGRATION_LOCK_WAIT_S = 300.0
MIGRATION_LOCK_POLL_S = 1.0

# --- Jobs-Prozess (NPT-12 K5) ---
# Heartbeat-Datei (JOBS_HEARTBEAT_FILE) aelter als das -> Liveness-Probe schlaegt fehl.
JOBS_HEARTBEAT_MAX_AGE_S = 120.0
# health_probe-Zeilen beendeter Worker (Pod-Generationen) nach dieser Zeit loeschen.
HEALTH_PROBE_RETENTION_S = 24 * 3600
HEALTH_PROBE_CLEANUP_INTERVAL_S = 3600.0


def check_ranking() -> None:
    """Prueft die Rangfolge der Grenzen; wirft ``RuntimeError`` bei Widerspruch."""
    problems: list[str] = []
    if REQUEST_SEMAPHORE > DB_POOL_SIZE + DB_MAX_OVERFLOW:
        problems.append("Semaphor groesser als Pool + Overflow")
    if REQUEST_SEMAPHORE + SSE_MAX_PER_WORKER >= UVICORN_LIMIT_CONCURRENCY:
        problems.append("Uvicorn-Grenze muss ueber Semaphor + SSE liegen")
    if SEMAPHORE_WAIT_S > DB_LOCK_TIMEOUT_MS / 1000 + DB_POOL_TIMEOUT_S:
        problems.append("Semaphor-Wartezeit laenger als Lock- plus Pool-Timeout")
    if HEALTH_READY_MAX_AGE_S <= HEALTH_PROBE_INTERVAL_S:
        problems.append("Ready-Fenster muss groesser als das Probe-Intervall sein")
    if HEALTH_STALL_EXIT_S <= HEALTH_READY_MAX_AGE_S:
        problems.append("Stillstand-Grenze muss ueber dem Ready-Fenster liegen")
    if SSE_HEARTBEAT_S >= UVICORN_KEEPALIVE_S:
        problems.append("SSE-Heartbeat muss unter dem Keepalive liegen")
    if SSE_SHUTDOWN_GRACE_S >= GUNICORN_GRACEFUL_TIMEOUT_S:
        problems.append("SSE-Abbruch beim Herunterfahren muss vor dem Gunicorn-Kill liegen")
    if GUNICORN_GRACEFUL_TIMEOUT_S >= SUPERVISOR_STOP_TIMEOUT_S:
        problems.append("Gunicorn-graceful_timeout muss unter der Supervisor-Stoppzeit liegen")
    if LIVE_BUNDLE_S * 1000 >= SSE_RETRY_MS:
        problems.append("Buendelfenster muss deutlich unter der Wiederverbindungszeit liegen")
    if problems:
        raise RuntimeError("limits.py inkonsistent: " + "; ".join(problems))
