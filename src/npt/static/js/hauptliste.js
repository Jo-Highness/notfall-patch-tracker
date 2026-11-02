/* Hauptliste eines Projekts (Auftrag 5.3/5.5/5.6): Tabulator-Grid, Inline-Editing der
 * *-Felder, Filter/Suche/Sortierung mit Zustand in der URL (Grammatik: npt/domain/filters.py),
 * Konfliktmeldung, optionale Namensabfrage, „Neues System anlegen", Live-Updates (npt-live.js).
 * Kein Inline-JS (CSP script-src 'self'); Zellinhalte werden nur als Text gesetzt (XSS).
 *
 * Live-Regeln (NPT-03): Eine Serverzeile wird nur angewendet, wenn ihr row_seq neuer ist.
 * Ein Feld, das gerade bearbeitet wird oder dessen Speichern läuft, wird nie überschrieben;
 * weicht der Serverwert vom bekannten Stand ab, wird die Zeile zurückgestellt und (bei
 * fokussiertem Feld) ein Hinweis mit „Übernehmen/Behalten" gezeigt. Filter und Sortierung
 * werden bei fremden Änderungen nicht automatisch neu angewendet (Zeilen springen nicht weg);
 * ein Knopf „Ansicht aktualisieren" erscheint, wenn gefilterte/sortierte Spalten betroffen sind.
 */
(function () {
  "use strict";

  var cfgEl = document.getElementById("npt-config");
  if (!cfgEl || typeof Tabulator === "undefined" || !window.NptFilter) { return; }
  var cfg = JSON.parse(cfgEl.textContent);
  var F = window.NptFilter;
  var base = cfg.base;
  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var csrfToken = csrfMeta ? csrfMeta.getAttribute("content") : "";
  var NAME_KEY = "npt.clientName";
  var DEBOUNCE_MS = 300;
  var EMAIL_RE = /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$/;

  var state = F.parse(window.location.search);
  var known = {};      // system_id -> {feld: zuletzt bestätigter Serverwert}
  var pending = {};    // "id\u0000feld" -> Speicherzustand
  var table = null;
  var cursor = null;   // change_seq des zuletzt übernommenen Stands
  var readOnly = !!cfg.readOnly;
  var stash = {};      // system_id -> zurückgestellte Serverzeile (Feld in Bearbeitung)
  var deferredNew = {};  // system_id -> neue Zeile, eingefügt sobald niemand editiert
  var reloadPending = false;
  var hint = null;     // {sid, field, value} des angezeigten Hinweises
  var live = null;
  var STATUS_TEXT = { aktiv: "aktiv", abgeschlossen: "abgeschlossen", archiviert: "archiviert" };
  var colByField = {};
  cfg.columns.forEach(function (c) { colByField[c.field] = c; });

  var $ = function (id) { return document.getElementById(id); };
  var statusLine = $("status-line");
  var counter = $("counter");

  function setStatus(text, kind) {
    statusLine.textContent = text;
    statusLine.className = "status-line" + (kind ? " status-" + kind : "");
  }
  function norm(v) { return (v === "" || v === undefined) ? null : v; }
  function label(field, value) {
    var v = norm(value);
    if (v === null) { return cfg.emptyLabel; }
    var col = colByField[field];
    return col && col.enum && col.enum[v] ? col.enum[v] : String(v);
  }
  function fieldTitle(field) { return colByField[field] ? colByField[field].title : field; }

  // ------------------------------------------------------------------ API
  function api(method, path, body) {
    return fetch(base + path, {
      method: method,
      headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRF-Token": csrfToken },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      cache: "no-store",
      referrerPolicy: "no-referrer"
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        return { status: r.status, body: j, retryAfter: parseInt(r.headers.get("Retry-After") || "0", 10) };
      });
    });
  }

  // ------------------------------------------------------------------ Name (optional)
  var namePromise = null;
  function storedName() {
    try { return window.localStorage.getItem(NAME_KEY); } catch (e) { return ""; }
  }
  function ensureName() {
    var current = storedName();
    if (current !== null) { return Promise.resolve(current || null); }
    if (namePromise) { return namePromise; }
    namePromise = new Promise(function (resolve) {
      var dlg = $("name-dialog");
      dlg.addEventListener("close", function onClose() {
        dlg.removeEventListener("close", onClose);
        var value = dlg.returnValue === "save" ? $("name-input").value.trim().slice(0, 100) : "";
        try { window.localStorage.setItem(NAME_KEY, value); } catch (e) { /* privat */ }
        namePromise = null;
        resolve(value || null);
      });
      dlg.showModal();
    });
    return namePromise;
  }

  // ------------------------------------------------------------------ Zellen-Feedback
  function cellOf(sid, field) {
    var row = table.getRow(sid);
    return row ? row.getCell(field) : null;
  }
  function mark(sid, field, kind) {
    var cell = cellOf(sid, field);
    if (!cell) { return; }
    var el = cell.getElement();
    el.classList.remove("cell-saving", "cell-saved", "cell-error");
    if (kind) { el.classList.add("cell-" + kind); }
    if (kind === "saved") {
      window.setTimeout(function () { el.classList.remove("cell-saved"); }, 1500);
    }
  }
  function setCellSilently(sid, field, value) {
    var row = table.getRow(sid);
    if (row) { var upd = {}; upd[field] = value; row.update(upd); }
  }

  // ------------------------------------------------------------------ Konflikt
  function askConflict(sid, field, current, mine) {
    return new Promise(function (resolve) {
      var dlg = $("conflict-dialog");
      $("conflict-field").textContent = fieldTitle(field);
      $("conflict-system").textContent = sid;
      $("conflict-current").textContent = label(field, current);
      $("conflict-mine").textContent = label(field, mine);
      dlg.addEventListener("close", function onClose() {
        dlg.removeEventListener("close", onClose);
        resolve(dlg.returnValue === "overwrite" ? "overwrite" : "take");
      });
      dlg.returnValue = "";
      dlg.showModal();
    });
  }

  // ------------------------------------------------------------------ Speichern
  function queueSave(cell) {
    var sid = cell.getRow().getData().system_id;
    var field = cell.getField();
    var key = sid + "\u0000" + field;
    var st = pending[key] || (pending[key] = { sid: sid, field: field, timer: null, busy: false, again: false, tries: 0 });
    st.value = norm(cell.getValue());
    mark(sid, field, "saving");
    window.clearTimeout(st.timer);
    st.timer = window.setTimeout(function () { flush(key); }, DEBOUNCE_MS);
  }

  function finish(key) {
    var st = pending[key];
    st.busy = false;
    if (st.again) { st.again = false; flush(key); } else { delete pending[key]; drain(); }
  }

  function flush(key) {
    var st = pending[key];
    if (!st) { return; }
    if (st.busy) { st.again = true; return; }
    st.busy = true;
    var sid = st.sid, field = st.field, value = st.value;
    var expected = known[sid] ? norm(known[sid][field]) : null;
    if (value === expected) { mark(sid, field, null); finish(key); return; }
    ensureName().then(function (name) {
      var body = { field: field, value: value, expected: expected };
      if (name) { body.client_name = name; }
      return api("PATCH", "/api/systems/" + encodeURIComponent(sid), body);
    }).then(function (res) {
      var b = res.body || {};
      if (res.status === 200) {
        known[sid][field] = norm(b.row[field]);
        applyServerRow(b.row, true);  // andere Felder ggf. mit fremden Änderungen
        mark(sid, field, st.again ? "saving" : "saved");
        setStatus("Gespeichert: " + sid + " · " + fieldTitle(field), "ok");
        st.tries = 0;
        finish(key);
      } else if (res.status === 409) {
        var current = norm(b.current);
        known[sid][field] = current;
        if (b.row) { applyServerRow(b.row, true); }
        askConflict(sid, field, current, value).then(function (choice) {
          if (choice === "overwrite") {
            st.again = true;  // erneut senden, expected = gezeigter aktueller Wert
          } else {
            setCellSilently(sid, field, current);
            mark(sid, field, null);
            setStatus("Aktueller Wert übernommen: " + sid + " · " + fieldTitle(field), "ok");
          }
          finish(key);
        });
      } else if ((res.status === 429 || res.status === 503) && st.tries < 5) {
        st.tries += 1;
        setStatus(res.status === 429 ? "Zu viele Änderungen in kurzer Zeit – wird gleich erneut gespeichert …"
          : "Server ausgelastet – wird erneut versucht …", "warn");
        var wait = Math.max(1, res.retryAfter || st.tries) * 1000;
        st.busy = false;
        window.setTimeout(function () { flush(key); }, wait);
      } else {
        if (b.reason === "schreibgeschuetzt") { setReadOnly(true, null); }
        // Zeile existiert lokal, Server kennt das Projekt nicht mehr -> Link ungültig
        if (b.reason === "nicht_gefunden" && res.status === 404 && table.getRow(sid)) { gone("token"); }
        var msg = b.reason === "schreibgeschuetzt" ? "Das Projekt ist schreibgeschützt."
          : b.reason === "csrf" ? "Sicherheitsprüfung fehlgeschlagen – bitte Seite neu laden."
          : (b.errors && b.errors[field]) ? b.errors[field]
          : "Speichern fehlgeschlagen (" + res.status + ").";
        setCellSilently(sid, field, known[sid][field]);
        mark(sid, field, "error");
        setStatus("Nicht gespeichert: " + sid + " · " + fieldTitle(field) + " – " + msg, "error");
        st.again = false;
        finish(key);
      }
    }).catch(function () {
      mark(sid, field, "error");
      setStatus("Keine Verbindung – Änderung an " + sid + " · " + fieldTitle(field) + " nicht gespeichert.", "error");
      st.again = false;
      finish(key);
    });
  }

  // ------------------------------------------------------------------ Filterzustand <-> URL
  function headerFilterValue(field) {
    if (F.isEnum(field)) { return state.enums[field] ? Object.keys(state.enums[field]) : null; }
    return state.contains[field] || null;
  }
  function readHeaderFilters() {
    state.contains = {};
    state.enums = {};
    table.getHeaderFilters().forEach(function (hf) {
      var v = hf.value;
      if (F.isEnum(hf.field)) {
        var keys = (Array.isArray(v) ? v : (v ? [v] : [])).filter(function (x) { return x !== "" && x !== null; });
        if (keys.length) {
          var set = {};
          keys.forEach(function (k) { set[k] = true; });
          state.enums[hf.field] = set;
        }
      } else if (typeof v === "string" && v.trim()) {
        state.contains[hf.field] = v.trim().slice(0, 500);
      }
    });
  }
  function syncUrl() {
    var q = F.toQuery(state);
    var url = window.location.pathname + (q ? "?" + q : "");
    window.history.replaceState(null, "", url);
    $("export-view").href = base + "/export.xlsx?umfang=sicht" + (q ? "&" + q : "");
  }
  function renderPrefilters() {
    var box = $("prefilters");
    box.textContent = "";
    var fieldsWithExact = Object.keys(state.exact);
    box.hidden = fieldsWithExact.length === 0;
    if (box.hidden) { return; }
    var lead = document.createElement("span");
    lead.textContent = "Vorfilter:";
    box.appendChild(lead);
    fieldsWithExact.forEach(function (field) {
      var chip = document.createElement("span");
      chip.className = "chip";
      var vals = state.exact[field].map(function (v) {
        return v === "" ? (field === "digital_access_zone" ? "(ohne DAZ)" : cfg.emptyLabel) : v;
      });
      var shown = vals.length > 3 ? vals.slice(0, 3).join(", ") + " … (+" + (vals.length - 3) + ")" : vals.join(", ");
      chip.textContent = fieldTitle(field) + " = " + shown + " ";
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chip-remove";
      btn.setAttribute("aria-label", "Vorfilter entfernen");
      btn.setAttribute("data-testid", "prefilter-remove-" + field);
      btn.textContent = "×";
      btn.addEventListener("click", function () {
        delete state.exact[field];
        renderPrefilters();
        table.refreshFilter();
      });
      chip.appendChild(btn);
      box.appendChild(chip);
    });
  }

  // ------------------------------------------------------------------ Spalten
  function textFormatter(cell) {
    var field = cell.getField();
    var v = cell.getValue();
    var span = document.createElement("span");
    var col = colByField[field];
    span.textContent = (v === null || v === undefined || v === "") ? "" : (col.enum ? label(field, v) : String(v));
    if (field === "remark" || field === "system_description") { span.className = "multiline"; }
    return span;
  }
  var WIDTHS = { system_id: 120, system_name: 200, sbf: 120, digital_access_zone: 150, affected: 130,
    installed_version: 130, action_taken: 190, remark: 260, system_description: 240, app_owner: 220,
    app_type: 140, lifecycle: 120, fqdn: 200, cio_unit: 140, foreign_key: 130, origin: 140 };

  function buildColumns() {
    return cfg.columns.map(function (c) {
      var def = {
        title: c.title, field: c.field, width: WIDTHS[c.field] || 140, formatter: textFormatter,
        headerSort: true,
        sorter: function (a, b, aRow, bRow) { return F.compareRows(aRow.getData(), bRow.getData(), c.field); },
        headerFilterFunc: function (hv, rv, rowData) {
          if (c.enum) {
            var keys = (Array.isArray(hv) ? hv : (hv ? [hv] : [])).filter(function (x) { return x !== ""; });
            if (!keys.length) { return true; }
            var set = {};
            keys.forEach(function (k) { set[k] = true; });
            return F.enumHit(rowData, c.field, set);
          }
          var text = typeof hv === "string" ? hv.trim().slice(0, 500) : "";
          return text ? F.matchesContains(rowData, c.field, text) : true;
        }
      };
      if (c.hint) {
        // Kurz-Tooltip am Spaltenkopf der *-Felder. Als Element mit textContent:
        // Tabulator setzt Strings per innerHTML ein (nie Nutzerdaten als Tooltip!).
        def.headerTooltip = function () {
          var tip = document.createElement("div");
          tip.textContent = c.hint;
          return tip;
        };
      }
      if (c.enum) {
        var values = {};
        values[cfg.emptyKey] = cfg.emptyLabel;
        Object.keys(c.enum).forEach(function (k) { values[k] = c.enum[k]; });
        def.headerFilter = "list";
        def.headerFilterParams = { values: values, multiselect: true, clearable: true };
        def.headerFilterPlaceholder = "alle";
      } else {
        def.headerFilter = "input";
        def.headerFilterPlaceholder = "enthält …";
      }
      if (c.editable) {
        def.cssClass = "editable editable-" + c.field;
        def.editable = function () { return !readOnly; };
        if (c.enum) {
          var opts = { "": cfg.emptyLabel };
          Object.keys(c.enum).forEach(function (k) { opts[k] = c.enum[k]; });
          def.editor = "list";
          def.editorParams = { values: opts };
        } else if (c.field === "remark") {
          def.editor = "textarea";
          def.editorParams = { elementAttributes: { maxlength: String(c.maxlength) }, verticalNavigation: "editor" };
        } else {
          def.editor = "input";
          def.editorParams = { elementAttributes: { maxlength: String(c.maxlength) } };
        }
      }
      return def;
    });
  }

  function initialHeaderFilters() {
    var out = [];
    cfg.columns.forEach(function (c) {
      var v = headerFilterValue(c.field);
      if (v !== null && v !== undefined && (!Array.isArray(v) || v.length)) { out.push({ field: c.field, value: v }); }
    });
    return out;
  }

  function updateCounter() {
    if (!table) { return; }
    var shown = table.getDataCount("active");
    var total = table.getDataCount();
    counter.textContent = shown + " von " + total + " Systemen angezeigt";
  }

  // ------------------------------------------------------------------ Aufbau
  function build(rows) {
    rows.forEach(function (r) {
      var k = {};
      cfg.editableFields.forEach(function (f) { k[f] = norm(r[f]); });
      known[r.system_id] = k;
    });
    var search = $("global-search");
    search.value = state.q || "";
    var opts = {
      data: rows,
      index: "system_id",
      height: "calc(100vh - 14.5rem)",
      layout: "fitData",
      renderVertical: "virtual",
      placeholder: "Keine Systeme für diese Filter.",
      columns: buildColumns(),
      initialHeaderFilter: initialHeaderFilters(),
      headerFilterLiveFilterDelay: 300,
      rowFormatter: function (row) {
        row.getElement().setAttribute("data-testid", "row-" + row.getData().system_id);
      }
    };
    if (state.sort) { opts.initialSort = [{ column: state.sort, dir: state.desc ? "desc" : "asc" }]; }
    $("grid").classList.toggle("is-readonly", readOnly);
    table = new Tabulator("#grid", opts);
    table.on("tableBuilt", function () {
      table.setFilter(function (data) {
        if (state.q && !F.matchesGlobal(data, state.q)) { return false; }
        for (var k in state.exact) {
          if (Object.prototype.hasOwnProperty.call(state.exact, k) && !F.matchesExact(data, k, state.exact[k])) { return false; }
        }
        return true;
      });
      renderPrefilters();
      updateCounter();
      syncUrl();
    });
    table.on("dataFiltered", function () {
      readHeaderFilters();
      syncUrl();
      setViewStale(false);
      window.setTimeout(updateCounter, 0);
    });
    table.on("dataSorted", function (sorters) {
      if (sorters && sorters.length) {
        state.sort = sorters[0].field;
        state.desc = sorters[0].dir === "desc";
      } else {
        state.sort = null;
        state.desc = false;
      }
      syncUrl();
    });
    table.on("cellEdited", function (cell) { queueSave(cell); });
    table.on("cellEditCancelled", function () { window.setTimeout(drain, 0); });

    var timer = null;
    search.addEventListener("input", function () {
      window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        state.q = search.value.trim().slice(0, 500) || null;
        table.refreshFilter();
      }, 250);
    });
    $("refresh-view").addEventListener("click", function () {
      setViewStale(false);
      table.refreshFilter();
    });
    $("reset-filters").addEventListener("click", function () {
      state.q = null;
      state.exact = {};
      search.value = "";
      renderPrefilters();
      table.clearHeaderFilter();
      table.refreshFilter();
    });
  }

  // ------------------------------------------------------------------ Neues System
  function setupCreate() {
    var dlg = $("new-system-dialog");
    var openBtn = $("new-system-open");
    if (!dlg || !openBtn) { return; }
    var form = $("new-system-form");
    var errBox = $("new-system-error");

    function fillSuggestions() {
      var data = table ? table.getData() : [];
      cfg.suggestFields.forEach(function (f) {
        var dl = document.getElementById("dl-" + f);
        if (!dl) { return; }
        var seen = {};
        data.forEach(function (r) { if (r[f]) { seen[r[f]] = true; } });
        dl.textContent = "";
        Object.keys(seen).sort().slice(0, 500).forEach(function (v) {
          var o = document.createElement("option");
          o.value = v;
          dl.appendChild(o);
        });
      });
    }
    function showErrors(errors) {
      form.querySelectorAll("[data-error-for]").forEach(function (el) {
        el.textContent = errors[el.getAttribute("data-error-for")] || "";
      });
    }
    function ownerValid(text) {
      var parts = text.split(/[;,]/).map(function (p) { return p.trim(); }).filter(Boolean);
      if (!parts.length) { return false; }
      return parts.every(function (p) {
        var m = /<([^<>]*)>/.exec(p);
        var addr = (m ? m[1] : p).trim();
        return addr.length <= 254 && EMAIL_RE.test(addr);
      });
    }
    function validate() {
      var errors = {};
      cfg.masterFields.forEach(function (f) {
        var v = form.elements[f].value.trim();
        if (!v) { errors[f] = "Pflichtfeld"; } else if (v.length > cfg.masterMax[f]) { errors[f] = "zu lang"; }
      });
      if (!errors.app_owner && !ownerValid(form.elements.app_owner.value)) {
        errors.app_owner = "muss eine gültige E-Mail-Adresse enthalten";
      }
      return errors;
    }

    openBtn.addEventListener("click", function () {
      fillSuggestions();
      showErrors({});
      errBox.hidden = true;
      dlg.showModal();
    });
    $("new-system-cancel").addEventListener("click", function () { dlg.close(); });
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var errors = validate();
      showErrors(errors);
      if (Object.keys(errors).length) { errBox.textContent = "Bitte die markierten Felder prüfen."; errBox.hidden = false; return; }
      errBox.hidden = true;
      var body = {};
      cfg.masterFields.concat(cfg.editableFields).forEach(function (f) {
        var v = form.elements[f].value;
        body[f] = v.trim() === "" ? null : v;
      });
      var submit = form.querySelector('button[type="submit"]');
      submit.disabled = true;
      ensureName().then(function (name) {
        if (name) { body.client_name = name; }
        return api("POST", "/api/systems", body);
      }).then(function (res) {
        var b = res.body || {};
        if (res.status === 201) {
          var r = b.row;
          var k = {};
          cfg.editableFields.forEach(function (f) { k[f] = norm(r[f]); });
          known[r.system_id] = k;
          var added = table.getRow(r.system_id) ? Promise.resolve() : table.addRow(r, true);
          return added.then(function () {
            table.scrollToRow(r.system_id, "top", false).catch(function () { /* ggf. ausgefiltert */ });
            updateCounter();
            form.reset();
            dlg.close();
            setStatus("Neues System angelegt: " + r.system_id, "ok");
          });
        }
        if (res.status === 422 && b.errors) { showErrors(b.errors); errBox.textContent = "Bitte die markierten Felder prüfen."; }
        else if (res.status === 429) { errBox.textContent = "Zu viele Neuanlagen in kurzer Zeit – bitte etwas warten."; }
        else if (b.reason === "schreibgeschuetzt") { errBox.textContent = "Das Projekt ist schreibgeschützt."; setReadOnly(true, null); }
        else { errBox.textContent = "Anlegen fehlgeschlagen (" + res.status + ")."; }
        errBox.hidden = false;
        return null;
      }).catch(function () {
        errBox.textContent = "Keine Verbindung – System wurde nicht angelegt.";
        errBox.hidden = false;
      }).then(function () { submit.disabled = false; });
    });
  }

  // ------------------------------------------------------------------ Live-Updates
  function isEditable(field) { return cfg.editableFields.indexOf(field) >= 0; }

  /** Gerade geöffneter Editor als {sid, field, cell} oder null (DOM-Zustand von Tabulator). */
  function currentEdit() {
    var el = document.querySelector("#grid .tabulator-cell.tabulator-editing");
    if (!el) { return null; }
    var rowEl = el.closest(".tabulator-row");
    var tid = rowEl ? rowEl.getAttribute("data-testid") || "" : "";
    if (tid.indexOf("row-") !== 0) { return null; }
    var sid = tid.slice(4), field = el.getAttribute("tabulator-field");
    return { sid: sid, field: field, cell: cellOf(sid, field) };
  }

  function flashCell(sid, field) {
    var cell = cellOf(sid, field);
    if (!cell) { return; }
    var el = cell.getElement();
    el.classList.remove("cell-flash");
    void el.offsetWidth;  // Animation neu starten
    el.classList.add("cell-flash");
    window.setTimeout(function () { el.classList.remove("cell-flash"); }, 1800);
  }
  function flashRow(sid) {
    var row = table.getRow(sid);
    if (!row) { return; }
    var el = row.getElement();
    el.classList.add("row-flash");
    window.setTimeout(function () { el.classList.remove("row-flash"); }, 2200);
  }

  function filterFields() {
    var out = {};
    Object.keys(state.contains).forEach(function (f) { out[f] = true; });
    Object.keys(state.enums).forEach(function (f) { out[f] = true; });
    Object.keys(state.exact).forEach(function (f) { out[f] = true; });
    if (state.sort) { out[state.sort] = true; }
    return out;
  }
  function setViewStale(on) {
    var btn = $("refresh-view");
    if (btn) { btn.hidden = !on; }
  }
  function noteChangedFields(changed) {
    if (!changed.length) { return; }
    var ff = filterFields();
    if (state.q || changed.some(function (f) { return ff[f]; })) { setViewStale(true); }
  }

  /** Serverzeile anwenden (SSE, Polling, eigene Antworten). Liefert die geänderten Felder. */
  function applyServerRow(r, flash) {
    var sid = r.system_id;
    var row = table.getRow(sid);
    if (!row) {
      if (currentEdit()) { deferredNew[sid] = r; return []; }
      addRemoteRow(r, flash);
      return [];
    }
    var cur = row.getData();
    if ((r.row_seq || 0) <= (cur.row_seq || 0)) { return []; }
    var ed = currentEdit();
    var k = known[sid] || (known[sid] = {});
    var upd = {}, changed = [], blocked = [];
    cfg.columns.forEach(function (c) {
      var f = c.field, nv = norm(r[f]);
      var busy = isEditable(f) && ((ed && ed.sid === sid && ed.field === f) || pending[sid + "\u0000" + f]);
      if (busy) {
        // nie unter dem Cursor bzw. während des Speicherns überschreiben
        if (nv !== norm(k[f])) { blocked.push(f); }
        return;
      }
      if (nv !== norm(cur[f])) { upd[f] = r[f] === undefined ? null : r[f]; changed.push(f); }
      if (isEditable(f)) { k[f] = nv; }
    });
    if (blocked.length) {
      stash[sid] = r;
    } else {
      upd.version = r.version;
      upd.row_seq = r.row_seq;
      delete stash[sid];
    }
    row.update(upd);
    if (flash) { changed.forEach(function (f) { flashCell(sid, f); }); }
    noteChangedFields(changed);
    if (ed && ed.sid === sid && blocked.indexOf(ed.field) >= 0) { showHint(sid, ed.field, norm(r[ed.field])); }
    return changed;
  }

  function addRemoteRow(r, flash) {
    var k = {};
    cfg.editableFields.forEach(function (f) { k[f] = norm(r[f]); });
    known[r.system_id] = k;
    table.addRow(r, false).then(function () {
      if (flash) { flashRow(r.system_id); }
      updateCounter();
    });
  }

  /** Zurückgestellte Zeilen/Neuanlagen/Neuladen nachholen, sobald nichts mehr blockiert. */
  function drain() {
    if (!table) { return; }
    var ed = currentEdit();
    if (hint && !(ed && ed.sid === hint.sid && ed.field === hint.field)) { hideHint(); }
    Object.keys(stash).forEach(function (sid) {
      var r = stash[sid];
      delete stash[sid];
      applyServerRow(r, true);
    });
    if (ed) { return; }
    if (reloadPending) { reloadPending = false; fullReload(); return; }
    var news = Object.keys(deferredNew);
    if (news.length) {
      news.forEach(function (sid) {
        var r = deferredNew[sid];
        delete deferredNew[sid];
        if (!table.getRow(sid)) { addRemoteRow(r, true); } else { applyServerRow(r, true); }
      });
    }
  }

  // Hinweis bei fokussiertem Feld (Auftrag 5.5)
  function showHint(sid, field, value) {
    hint = { sid: sid, field: field, value: value };
    $("live-hint-text").textContent = "Wert wurde von anderer Person geändert: " + fieldTitle(field) +
      " von " + sid + " ist jetzt „" + label(field, value) + "“.";
    $("live-hint").hidden = false;
  }
  function hideHint() {
    hint = null;
    $("live-hint").hidden = true;
  }
  function setupHint() {
    ["live-hint-take", "live-hint-keep"].forEach(function (id) {
      // mousedown nicht weitergeben: der Editor behält den Fokus (kein ungewolltes Speichern)
      $(id).addEventListener("mousedown", function (ev) { ev.preventDefault(); });
    });
    $("live-hint-take").addEventListener("click", function () {
      if (!hint) { return; }
      var h = hint;
      hideHint();
      var cell = cellOf(h.sid, h.field);
      if (cell) { cell.cancelEdit(); }
      known[h.sid][h.field] = h.value;
      setCellSilently(h.sid, h.field, h.value);
      flashCell(h.sid, h.field);
      window.setTimeout(drain, 0);
      setStatus("Wert der anderen Person übernommen: " + h.sid + " · " + fieldTitle(h.field), "ok");
    });
    $("live-hint-keep").addEventListener("click", function () {
      if (!hint) { return; }
      // bewusst behalten: der gezeigte Wert gilt als bekannt, Speichern überschreibt ihn
      known[hint.sid][hint.field] = hint.value;
      hideHint();
      var ed = currentEdit();
      if (ed && ed.cell) {
        var input = ed.cell.getElement().querySelector("input, textarea");
        if (input) { input.focus(); }
      }
    });
  }

  function setReadOnly(on, status) {
    var banner = $("live-banner");
    var openBtn = $("new-system-open");
    if (on) {
      readOnly = true;
      $("grid").classList.add("is-readonly");
      var ed = currentEdit();
      if (ed && ed.cell) { ed.cell.cancelEdit(); }
      if (openBtn) { openBtn.hidden = true; }
      var dlg = $("new-system-dialog");
      if (dlg && dlg.open) { dlg.close(); }
      banner.textContent = "Dieses Projekt ist " + (STATUS_TEXT[status] || "abgeschlossen") +
        " und schreibgeschützt. Änderungen sind nicht mehr möglich.";
      banner.hidden = false;
    } else if (cfg.readOnly) {
      // Seite wurde schreibgeschützt geladen (ohne Editoren): Neuladen nötig
      banner.textContent = "Das Projekt wurde wieder geöffnet. Bitte die Seite neu laden, um zu bearbeiten.";
      banner.hidden = false;
    } else {
      readOnly = false;
      $("grid").classList.remove("is-readonly");
      if (openBtn) { openBtn.hidden = false; }
      banner.hidden = true;
    }
  }
  function applyProject(p) {
    if (!p) { return; }
    if (!!p.read_only !== readOnly || (cfg.readOnly && !p.read_only)) { setReadOnly(!!p.read_only, p.status); }
  }

  function gone(reason) {
    if (live) { live.stop(); live = null; }
    readOnly = true;
    var ed = currentEdit();
    if (ed && ed.cell) { ed.cell.cancelEdit(); }
    hideHint();
    ["new-system-open", "export-view", "export-all"].forEach(function (id) { var e = $(id); if (e) { e.hidden = true; } });
    var banner = $("live-banner");
    banner.textContent = reason === "geloescht"
      ? "Dieses Projekt wurde gelöscht."
      : "Dieser Projekt-Link ist nicht mehr gültig (vom Admin neu erzeugt). Bitte den aktuellen Link anfordern.";
    banner.hidden = false;
    setStatus("", "");
  }

  function applyRows(rows) {
    rows.forEach(function (r) { applyServerRow(r, true); });
  }

  function fullReload() {
    if (currentEdit()) { reloadPending = true; return Promise.resolve(); }
    return fetch(base + "/api/systems", { headers: { "Accept": "application/json" }, cache: "no-store", credentials: "same-origin" })
      .then(function (r) {
        if (r.status === 404) { gone("token"); return null; }
        if (!r.ok) { throw new Error(String(r.status)); }
        return r.json();
      })
      .then(function (data) {
        if (!data) { return; }
        known = {};
        stash = {};
        deferredNew = {};
        data.rows.forEach(function (r) {
          var k = {};
          cfg.editableFields.forEach(function (f) { k[f] = norm(r[f]); });
          known[r.system_id] = k;
        });
        cursor = data.seq;
        applyProject(data.project);
        return table.replaceData(data.rows).then(function () {
          updateCounter();
          setStatus("Liste wurde vollständig neu geladen (Datenstand wiederhergestellt).", "warn");
        });
      });
  }

  function onDelta(d) {
    applyRows(d.rows || []);
    applyProject(d.project);
    if (typeof d.seq === "number" && (cursor === null || d.seq > cursor)) { cursor = d.seq; }
    if (d.rows && d.rows.length) { updateCounter(); }
  }

  function poll() {
    if (cursor === null) { return Promise.resolve(); }
    return fetch(base + "/api/systems?updated_since=" + encodeURIComponent(cursor), {
      headers: { "Accept": "application/json" }, cache: "no-store", credentials: "same-origin"
    }).then(function (r) {
      if (r.status === 404) { gone("token"); return null; }
      if (!r.ok) { throw new Error(String(r.status)); }
      return r.json();
    }).then(function (data) {
      if (!data) { return; }
      if (data.full_reload) { return fullReload(); }
      onDelta(data);
    });
  }

  function startLive() {
    if (!window.NptLive) { return; }
    setupHint();
    window.setInterval(drain, 400);
    live = window.NptLive.start({
      base: base,
      cursor: function () { return cursor; },
      onDelta: onDelta,
      onReload: function () { fullReload(); },
      onGone: gone,
      poll: poll
    });
  }

  // ------------------------------------------------------------------ Start
  var t0 = window.performance.now();
  fetch(base + "/api/systems", { headers: { "Accept": "application/json" }, cache: "no-store", credentials: "same-origin" })
    .then(function (r) {
      if (!r.ok) { throw new Error(String(r.status)); }
      return r.json();
    })
    .then(function (data) {
      cursor = data.seq;
      build(data.rows);
      setStatus(data.rows.length + " Systeme geladen (" + ((window.performance.now() - t0) / 1000).toFixed(1) + " s).", "");
      setupCreate();
      startLive();
    })
    .catch(function (e) {
      counter.textContent = "";
      setStatus("Liste konnte nicht geladen werden (" + e.message + "). Bitte Seite neu laden.", "error");
    });
}());
