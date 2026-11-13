/* Notfall-Patch-Tracker – Rich-Text-Editor (Quill 2) fuer News/FAQ (CSP: kein Inline-JS).
 * Markup: <form data-richtext-form> mit <div data-richtext-editor hidden><div></div></div> und
 * <textarea data-richtext-source>. Ohne JS/Quill bleibt das Textfeld (HTML) nutzbar; der Server
 * sanitisiert in jedem Fall (nh3-Allowlist). */
"use strict";
(function () {
  function init(form) {
    var wrap = form.querySelector("[data-richtext-editor]");
    var textarea = form.querySelector("[data-richtext-source]");
    var holder = wrap ? wrap.firstElementChild : null;
    if (!wrap || !holder || !(textarea instanceof HTMLTextAreaElement) || typeof window.Quill !== "function") {
      return;
    }
    var quill = new window.Quill(holder, {
      theme: "snow",
      formats: ["header", "bold", "italic", "list", "link"],
      modules: {
        keyboard: window.nptQuillKeyboard, // Tab verlaesst den Editor (WCAG 2.1.1, app.js)
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
    if (window.nptQuillA11y) { window.nptQuillA11y(quill, form.querySelector('label[for="richtext-source"]')); }
    var sync = function () {
      textarea.value = quill.getText().trim() === "" ? "" : quill.getSemanticHTML();
    };
    quill.on("text-change", sync);
    form.addEventListener("submit", sync);
    window.nptRichtextEditor = quill; // fuer E2E-Tests
  }

  document.addEventListener("DOMContentLoaded", function () {
    var forms = document.querySelectorAll("form[data-richtext-form]");
    for (var i = 0; i < forms.length; i++) { init(forms[i]); }
  });
})();
