/* Übersicht (Pivot) eines Projekts (Auftrag 5.4): live über denselben SSE-Stream wie die
 * Hauptliste (npt-live.js). Änderungen lösen ein gebündeltes Neuladen von /api/pivot aus:
 * höchstens einmal je Sekunde (erste Änderung nach Ruhe sofort, weitere gesammelt), plus
 * bis zu 300 ms Streuung, damit viele offene Übersichten nicht gleichzeitig laden.
 * Die Tabelle wird per DOM-API neu aufgebaut (nur textContent, kein innerHTML).
 */
(function () {
  "use strict";

  var cfgEl = document.getElementById("npt-pivot-config");
  if (!cfgEl) { return; }
  var cfg = JSON.parse(cfgEl.textContent);
  var base = cfg.base;
  var cursor = cfg.seq;
  var BUNDLE_MS = 1000, JITTER_MS = 300;
  var timer = null, loading = false, again = false, live = null, lastLoad = Date.now();
  var $ = function (id) { return document.getElementById(id); };

  function fmtPct(v) { return v.toFixed(1).replace(".", ",") + " %"; }
  function stamp() {
    var d = new Date();
    $("pivot-stand").textContent = "Stand: " + d.toLocaleTimeString("de-DE");
  }

  function link(href, text, testid) {
    var a = document.createElement("a");
    a.href = href;
    a.textContent = String(text);
    a.setAttribute("data-testid", testid);
    return a;
  }
  function numCell(href, count, testid, extra) {
    var td = document.createElement("td");
    td.className = "num" + (extra ? " " + extra : "") + (count ? "" : " zero");
    td.appendChild(link(href, count, testid));
    return td;
  }

  function previousCounts() {
    var out = {};
    document.querySelectorAll("#pivot a[data-testid]").forEach(function (a) {
      out[a.getAttribute("data-testid") + "\u0000" + (a.closest("tr").getAttribute("data-daz") || "")] = a.textContent;
    });
    return out;
  }

  function render(p) {
    var before = previousCounts();
    var body = $("pivot-body");
    var foot = $("pivot-foot");
    var tbody = document.createElement("tbody");
    tbody.id = "pivot-body";
    p.rows.forEach(function (r, ri) {
      var tr = document.createElement("tr");
      tr.setAttribute("data-daz", r.daz || "");
      tr.setAttribute("data-testid", "pivot-row-" + ri);
      var th = document.createElement("th");
      th.scope = "row";
      th.appendChild(link(r.href, r.label, "rowlabel-" + ri));
      tr.appendChild(th);
      r.cells.forEach(function (c) { tr.appendChild(numCell(c.href, c.count, "cell-" + ri + "-" + (c.key || "leer"))); });
      tr.appendChild(numCell(r.href, r.total, "rowsum-" + ri, "sum"));
      tbody.appendChild(tr);
    });
    var tfoot = document.createElement("tfoot");
    tfoot.id = "pivot-foot";
    var ftr = document.createElement("tr");
    var fth = document.createElement("th");
    fth.scope = "row";
    fth.textContent = "Summe";
    ftr.appendChild(fth);
    p.columns.forEach(function (c) { ftr.appendChild(numCell(c.href, c.count, "colsum-" + (c.key || "leer"), "sum")); });
    ftr.appendChild(numCell(p.total_href, p.total, "total", "sum total"));
    tfoot.appendChild(ftr);
    body.replaceWith(tbody);
    foot.replaceWith(tfoot);
    // geänderte Zahlen kurz hervorheben
    document.querySelectorAll("#pivot td a[data-testid]").forEach(function (a) {
      var key = a.getAttribute("data-testid") + "\u0000" + (a.closest("tr").getAttribute("data-daz") || "");
      if (Object.prototype.hasOwnProperty.call(before, key) && before[key] !== a.textContent) {
        a.parentNode.classList.add("changed");
      }
    });
    var k = p.kpi;
    $("kpi-processed").href = k.processed_href;
    $("kpi-processed-pct").textContent = fmtPct(k.processed_pct);
    $("kpi-processed-count").textContent = String(k.processed);
    $("kpi-total").textContent = String(k.total);
    $("kpi-yes").href = k.yes_without_action_href;
    $("kpi-yes-count").textContent = String(k.yes_without_action);
    stamp();
  }

  function gone(reason) {
    if (live) { live.stop(); live = null; }
    var banner = $("live-banner");
    banner.textContent = reason === "geloescht" ? "Dieses Projekt wurde gelöscht."
      : "Dieser Projekt-Link ist nicht mehr gültig (vom Admin neu erzeugt). Bitte den aktuellen Link anfordern.";
    banner.hidden = false;
  }

  function load() {
    if (loading) { again = true; return Promise.resolve(); }
    loading = true;
    lastLoad = Date.now();
    return fetch(base + "/api/pivot", { headers: { "Accept": "application/json" }, cache: "no-store", credentials: "same-origin" })
      .then(function (r) {
        if (r.status === 404) { gone("token"); return null; }
        if (!r.ok) { throw new Error(String(r.status)); }
        return r.json();
      })
      .then(function (p) {
        if (p) {
          if (typeof p.seq === "number" && p.seq > cursor) { cursor = p.seq; }
          render(p);
        }
      })
      .then(function () {
        loading = false;
        if (again) { again = false; schedule(); }
      }, function (e) {
        loading = false;
        throw e;
      });
  }

  function schedule() {
    if (timer) { return; }
    var wait = Math.max(0, lastLoad + BUNDLE_MS - Date.now()) + Math.round(Math.random() * JITTER_MS);
    timer = window.setTimeout(function () { timer = null; load(); }, wait);
  }

  stamp();
  if (!window.NptLive) { return; }
  live = window.NptLive.start({
    base: base,
    cursor: function () { return cursor; },
    onDelta: function (d) {
      if (typeof d.seq === "number" && d.seq > cursor) { cursor = d.seq; }
      if ((d.rows && d.rows.length) || d.project) { schedule(); }
    },
    onReload: schedule,
    onGone: gone,
    poll: load
  });
}());
