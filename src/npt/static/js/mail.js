/* Notfall-Patch-Tracker – Admin-Mailversand: Quill-2-Editor und Statusanzeige (CSP: kein Inline-JS). */
"use strict";
(function () {
  // ---------------------------------------------------------------- Editor
  function initEditor() {
    var wrap = document.getElementById("mail-editor-wrap");
    var holder = document.getElementById("mail-editor");
    var textarea = document.getElementById("mail-body");
    var form = document.getElementById("mail-form");
    if (!wrap || !holder || !(textarea instanceof HTMLTextAreaElement) || !form || typeof window.Quill !== "function") {
      return; // ohne JS/Quill bleibt das HTML-Textfeld nutzbar (serverseitig sanitisiert)
    }
    var quill = new window.Quill(holder, {
      theme: "snow",
      formats: ["header", "bold", "italic", "list", "link"],
      modules: {
        toolbar: [
          [{ header: [1, 2, 3, false] }],
          ["bold", "italic"],
          [{ list: "ordered" }, { list: "bullet" }],
          ["link"],
          ["clean"]
        ]
      }
    });
    if (textarea.value.trim() !== "") {
      quill.setContents(quill.clipboard.convert({ html: textarea.value }), "silent");
    }
    textarea.hidden = true;
    wrap.hidden = false;
    var sync = function () {
      textarea.value = quill.getText().trim() === "" ? "" : quill.getSemanticHTML();
    };
    quill.on("text-change", sync);
    form.addEventListener("submit", sync);
    var btn = document.getElementById("mail-insert-link");
    if (btn) {
      btn.hidden = false;
      btn.addEventListener("click", function () {
        var range = quill.getSelection(true);
        var index = range ? range.index : quill.getLength() - 1;
        quill.insertText(index, "[LINK]", "user");
        quill.setSelection(index + 6, 0, "user");
      });
    }
    window.nptMailEditor = quill; // fuer E2E-Tests
  }

  // ---------------------------------------------------------------- Status (Auto-Refresh)
  var STATUS_POLL_MS = 3000;

  function formatTime(iso) {
    if (!iso) { return ""; }
    try {
      return new Date(iso).toLocaleString("de-DE", { timeZone: "Europe/Berlin" });
    } catch (e) {
      return iso;
    }
  }

  function applyStatus(section, data) {
    var counts = data.counts || {};
    Object.keys(counts).forEach(function (key) {
      var el = section.querySelector('[data-count="' + key + '"]');
      if (el) { el.textContent = String(counts[key]); }
    });
    (data.deliveries || []).forEach(function (d) {
      var row = section.querySelector('tr[data-delivery-id="' + d.id + '"]');
      if (!row) { return; }
      var st = row.querySelector('[data-field="status"]');
      if (st) {
        st.textContent = d.status_label;
        st.className = "mail-st-" + d.status;
      }
      var at = row.querySelector('[data-field="attempts"]');
      if (at) { at.textContent = String(d.attempts); }
      var sent = row.querySelector('[data-field="sent_at"]');
      if (sent) { sent.textContent = formatTime(d.sent_at); }
      var err = row.querySelector('[data-field="error"]');
      if (err) { err.textContent = d.error; }
    });
  }

  function initStatus() {
    var section = document.querySelector("[data-status-url]");
    if (!section) { return; }
    var url = section.getAttribute("data-status-url");
    var poll = function () {
      fetch(url, { credentials: "same-origin", cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) { window.setTimeout(poll, STATUS_POLL_MS * 2); return; }
          applyStatus(section, data);
          if (data.active) {
            window.setTimeout(poll, STATUS_POLL_MS);
          } else {
            // Lauf abgeschlossen: Seite neu laden (Formulare fuer erneuten Versand).
            window.location.replace(window.location.pathname + "?lauf=" + String(data.send_run));
          }
        })
        .catch(function () { window.setTimeout(poll, STATUS_POLL_MS * 2); });
    };
    window.setTimeout(poll, STATUS_POLL_MS);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initEditor();
    initStatus();
  });
})();
