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
    window.setTimeout(function () { btn.textContent = old; }, 1500);
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(input.value).then(done, function () { input.select(); });
  } else {
    input.select();
    try { document.execCommand("copy"); done(); } catch (e) { /* Auswahl bleibt markiert */ }
  }
});
