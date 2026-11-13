/* Notfall-Patch-Tracker – FAQ: Live-Filter beim Tippen (CSP: kein Inline-JS).
 * Ohne JS sucht das Formular serverseitig (?q=). Semantik wie der Server: jedes Wort
 * (durch Leerraum getrennt) muss in Frage oder Antwort vorkommen, ohne Gross-/Kleinschreibung. */
"use strict";
(function () {
  document.addEventListener("DOMContentLoaded", function () {
    var input = document.getElementById("faq-q");
    var count = document.getElementById("faq-count");
    var empty = document.getElementById("faq-empty");
    var items = document.querySelectorAll(".faq-item");
    if (!(input instanceof HTMLInputElement) || items.length === 0) { return; }
    var total = items.length;
    var apply = function () {
      var words = input.value.toLowerCase().trim().split(/\s+/).filter(function (w) { return w !== ""; });
      var shown = 0;
      for (var i = 0; i < items.length; i++) {
        var hay = (items[i].textContent || "").toLowerCase(); // nur Text, nie HTML
        var hit = words.every(function (w) { return hay.indexOf(w) !== -1; });
        items[i].hidden = !hit;
        if (hit) {
          shown++;
          if (words.length > 0) { items[i].open = true; }
        }
      }
      if (count) { count.textContent = words.length ? shown + " von " + total + " Einträgen" : total + " Einträge"; }
      if (empty) { empty.hidden = shown !== 0; }
    };
    input.addEventListener("input", apply);
  });
})();
