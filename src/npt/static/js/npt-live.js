/* Live-Verbindung eines Projekts (NPT-03, Auftrag 5.5): EventSource auf /p/<token>/api/events,
 * automatisches Wiederverbinden mit Last-Event-ID (Browser), bei HTTP-Fehler (503/404) eigener
 * Neuaufbau mit ?since=<Cursor> und Backoff; solange der Stream nicht offen ist, Polling alle 5 s
 * über die Seite (opts.poll). Verbindungsstatus im Kopf (#live-status). Kein Inline-JS.
 * Nutzung: NptLive.start({ base, cursor: fn, onDelta, onReload, onGone, poll: fn -> Promise })
 */
(function (root) {
  "use strict";

  var POLL_MS = 5000;
  var REOPEN_MIN_MS = 5000;
  var REOPEN_MAX_MS = 60000;
  var LABELS = {
    connecting: "Verbinde …",
    live: "Live",
    polling: "Abgleich alle 5 s",
    offline: "Keine Verbindung",
    gone: "Getrennt"
  };

  function start(opts) {
    var el = document.getElementById("live-status");
    var es = null, pollTimer = null, reopenTimer = null, stopped = false;
    var backoff = REOPEN_MIN_MS, state = "", polling = false;

    function setState(s) {
      if (s === state) { return; }
      state = s;
      if (el) {
        el.hidden = false;
        el.textContent = LABELS[s] || s;
        el.className = "live-status live-" + s;
        el.setAttribute("data-state", s);
        el.title = s === "live" ? "Änderungen anderer erscheinen sofort."
          : s === "polling" ? "Live-Verbindung gestört – die Liste wird alle 5 Sekunden abgeglichen."
          : "";
      }
      if (opts.onState) { opts.onState(s); }
    }

    function pollOnce() {
      if (stopped || polling) { return; }
      polling = true;
      Promise.resolve(opts.poll()).then(function () {
        polling = false;
        if (!stopped && (!es || es.readyState !== 1)) { setState("polling"); }
      }, function () {
        polling = false;
        if (!stopped && (!es || es.readyState !== 1)) { setState("offline"); }
      });
    }
    function startPolling() {
      if (pollTimer || stopped) { return; }
      pollTimer = window.setInterval(pollOnce, POLL_MS);
    }
    function stopPolling() {
      if (pollTimer) { window.clearInterval(pollTimer); pollTimer = null; }
    }

    function scheduleReopen() {
      if (reopenTimer || stopped) { return; }
      var wait = Math.round(backoff * (0.5 + Math.random()));
      backoff = Math.min(backoff * 2, REOPEN_MAX_MS);
      reopenTimer = window.setTimeout(function () { reopenTimer = null; open(); }, wait);
    }

    function parse(e) {
      try { return JSON.parse(e.data); } catch (err) { return null; }
    }

    function open() {
      if (stopped) { return; }
      if (typeof window.EventSource === "undefined") { setState("polling"); startPolling(); return; }
      var c = opts.cursor();
      var url = opts.base + "/api/events" + (c === null || c === undefined ? "" : "?since=" + encodeURIComponent(c));
      es = new window.EventSource(url);
      setState("connecting");
      es.onopen = function () {
        backoff = REOPEN_MIN_MS;
        stopPolling();
        setState("live");
      };
      es.addEventListener("delta", function (e) { var d = parse(e); if (d) { opts.onDelta(d); } });
      es.addEventListener("reload", function (e) { var d = parse(e); if (d) { opts.onReload(d); } });
      es.addEventListener("gone", function (e) {
        var d = parse(e) || {};
        stop();
        setState("gone");
        opts.onGone(d.reason || "token");
      });
      es.onerror = function () {
        if (stopped) { return; }
        startPolling();
        if (es.readyState === 2) {
          // HTTP-Fehler (z. B. 503 voll, 404 Link ungültig): kein automatisches Neuverbinden.
          es = null;
          setState("polling");
          pollOnce();
          scheduleReopen();
        } else {
          setState("connecting");  // Browser verbindet selbst neu (mit Last-Event-ID)
        }
      };
    }

    function stop() {
      stopped = true;
      stopPolling();
      if (reopenTimer) { window.clearTimeout(reopenTimer); reopenTimer = null; }
      if (es) { es.close(); es = null; }
    }

    open();
    return {
      stop: function () { stop(); setState("gone"); },
      state: function () { return state; }
    };
  }

  root.NptLive = { start: start, POLL_MS: POLL_MS };
}(this));
