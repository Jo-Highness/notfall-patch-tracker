/* Notfall-Patch-Tracker – gemeinsames Skript (kein Inline-JS, CSP script-src 'self'). */
"use strict";
document.addEventListener("submit", function (ev) {
  var form = ev.target;
  if (!(form instanceof HTMLFormElement)) { return; }
  var question = form.getAttribute("data-confirm");
  if (question && !window.confirm(question)) { ev.preventDefault(); }
});
// „alle auswählen": <input type="checkbox" data-check-all="<name>"> schaltet alle Checkboxen name=<name>.
document.addEventListener("change", function (ev) {
  var box = ev.target;
  if (!(box instanceof HTMLInputElement)) { return; }
  var name = box.getAttribute("data-check-all");
  if (!name || !box.form) { return; }
  var items = box.form.querySelectorAll('input[type="checkbox"][name="' + name + '"]');
  for (var i = 0; i < items.length; i++) { items[i].checked = box.checked; }
});
// Gruppe: <input type="checkbox" data-check-group="<g>"> schaltet alle Checkboxen mit data-group=<g>.
document.addEventListener("change", function (ev) {
  var box = ev.target;
  if (!(box instanceof HTMLInputElement)) { return; }
  var group = box.getAttribute("data-check-group");
  if (!group || !box.form) { return; }
  var items = box.form.querySelectorAll('input[type="checkbox"][data-group="' + group + '"]');
  for (var i = 0; i < items.length; i++) { items[i].checked = box.checked; }
});
// Kopieren: <button data-copy-target="<id>"> kopiert den Wert des Eingabefelds <id>.
document.addEventListener("click", function (ev) {
  var btn = ev.target;
  if (!(btn instanceof HTMLElement)) { return; }
  var id = btn.getAttribute("data-copy-target");
  if (!id) { return; }
  var input = document.getElementById(id);
  if (!(input instanceof HTMLInputElement)) { return; }
  var done = function () {
    var old = btn.textContent;
    btn.textContent = "Kopiert";
    window.nptAnnounce("In die Zwischenablage kopiert");
    window.setTimeout(function () { btn.textContent = old; }, 1500);
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(input.value).then(done, function () { input.select(); });
  } else {
    input.select();
    try { document.execCommand("copy"); done(); } catch (e) { /* Auswahl bleibt markiert */ }
  }
});

// ---------------------------------------------------------------- Barrierefreiheit (NPT-16)
// Kurze Ansage fuer Screenreader (WCAG 4.1.3) ueber die globale Live-Region in base.html.
window.nptAnnounce = function (text) {
  var box = document.getElementById("sr-status");
  if (!box) { return; }
  box.textContent = "";
  window.setTimeout(function () { box.textContent = text; }, 50);
};
// Quill: deutsche Namen fuer Werkzeugleiste und Auswahl, Editor mit Namen, Tab verlaesst den
// Editor (WCAG 2.1.1, 4.1.2). Aufruf nach new Quill(...); labelEl = sichtbare Beschriftung.
window.nptQuillKeyboard = {
  bindings: {
    tab: { key: "Tab", handler: function () { return true; } },
    indent: { key: "Tab", format: ["blockquote", "indent", "list"], handler: function () { return true; } },
    outdent: { key: "Tab", shiftKey: true, format: ["blockquote", "indent", "list"], handler: function () { return true; } }
  }
};
window.nptQuillA11y = function (quill, labelEl) {
  var root = quill.root;
  root.setAttribute("role", "textbox");
  root.setAttribute("aria-multiline", "true");
  if (labelEl) {
    if (!labelEl.id) { labelEl.id = "lbl-" + Math.random().toString(36).slice(2); }
    root.setAttribute("aria-labelledby", labelEl.id);
    labelEl.addEventListener("click", function () { quill.focus(); });
  }
  var bar = quill.getModule("toolbar");
  var tb = bar && bar.container;
  if (!tb) { return; }
  tb.setAttribute("role", "toolbar");
  tb.setAttribute("aria-label", "Formatierung");
  var names = {
    ".ql-bold": "Fett", ".ql-italic": "Kursiv", '.ql-list[value="ordered"]': "Nummerierte Liste",
    '.ql-list[value="bullet"]': "Aufzählung", ".ql-link": "Link einfügen", ".ql-clean": "Formatierung entfernen"
  };
  Object.keys(names).forEach(function (sel) {
    tb.querySelectorAll(sel).forEach(function (b) { b.setAttribute("aria-label", names[sel]); b.setAttribute("title", names[sel]); });
  });
  tb.querySelectorAll(".ql-header .ql-picker-label").forEach(function (l) {
    l.setAttribute("aria-label", "Absatzformat");
  });
  tb.querySelectorAll(".ql-header .ql-picker-item").forEach(function (it) {
    var v = it.getAttribute("data-value");
    it.setAttribute("aria-label", v ? "Überschrift " + v : "Normaler Text");
  });
};
document.addEventListener("DOMContentLoaded", function () {
  // Breite Datentabellen in einen eigenen, per Tastatur scrollbaren Bereich (WCAG 1.4.10, 2.1.1)
  var tables = document.querySelectorAll("main table");
  for (var i = 0; i < tables.length; i++) {
    var t = tables[i];
    if (t.closest(".table-scroll") || t.closest("#grid")) { continue; }
    var wrap = document.createElement("div");
    wrap.className = "table-scroll";
    t.parentNode.insertBefore(wrap, t);
    wrap.appendChild(t);
    if (wrap.scrollWidth > wrap.clientWidth + 1) {
      wrap.setAttribute("tabindex", "0");
      wrap.setAttribute("role", "region");
      var cap = t.querySelector("caption");
      var head = t.closest("section, .card");
      var h = head ? head.querySelector("h1, h2, h3") : null;
      wrap.setAttribute("aria-label", (cap || h) ? (cap || h).textContent.trim() + " (Tabelle, waagerecht scrollbar)" : "Tabelle, waagerecht scrollbar");
    }
  }
  // Codebeispiele (Hilfe) scrollen waagerecht: per Tastatur erreichbar machen (WCAG 2.1.1)
  var pres = document.querySelectorAll("main pre");
  for (var k = 0; k < pres.length; k++) {
    pres[k].setAttribute("tabindex", "0");
    pres[k].setAttribute("role", "region");
    pres[k].setAttribute("aria-label", "Codebeispiel");
  }
  // Serverseitige Fehlermeldung: Fokus darauf, damit sie sofort gelesen wird (WCAG 3.3.1)
  var err = document.querySelector("main .msg-err[role=alert]");
  if (err && !err.hidden) {
    err.setAttribute("tabindex", "-1");
    err.focus();
  }
});
