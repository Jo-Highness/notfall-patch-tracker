// Prueft persistQuery/hasViewParams aus static/js/npt-filter.js (NPT-17, gemerkter Filter).
// Aufruf: node tests/js/filter-persist.test.js   (Exitcode 0 = alle bestanden)
"use strict";
const path = require("path");
const F = require(path.join(__dirname, "..", "..", "src", "npt", "static", "js", "npt-filter.js"));
let failed = 0;
function eq(name, got, want) {
  if (got !== want) { failed++; console.log(`FEHLER ${name}: ${JSON.stringify(got)} != ${JSON.stringify(want)}`); }
}
// Link-Vorfilter x.* werden nie gemerkt, Suche/Spaltenfilter/Sortierung schon
const st = F.parse("q=Schmidt&f.app_owner=schmidt&f.affected=ja&x.system_id=SYS-1&sort=system_name&dir=desc");
eq("persist ohne x.*", F.persistQuery(st), "q=Schmidt&f.affected=ja&f.app_owner=schmidt&sort=system_name&dir=desc");
eq("nur Vorfilter -> leer", F.persistQuery(F.parse("x.system_id=SYS-1&x.system_id=SYS-2")), "");
eq("leer", F.persistQuery(F.parse("")), "");
// Rundlauf: gemerkter Wert ergibt wieder denselben Zustand
const again = F.parse(F.persistQuery(st));
eq("Rundlauf", F.persistQuery(again), F.persistQuery(st));
eq("Rundlauf ohne exact", Object.keys(again.exact).length, 0);
// Vorrang der URL
eq("URL q", F.hasViewParams("?q=a"), true);
eq("URL Vorfilter", F.hasViewParams("?x.system_id=1"), true);
eq("URL Spaltenfilter", F.hasViewParams("?f.sbf=1"), true);
eq("URL Sortierung", F.hasViewParams("?sort=system_id"), true);
eq("URL ohne Filter", F.hasViewParams(""), false);
eq("URL fremder Parameter", F.hasViewParams("?foo=1"), false);
// Unbekannte/manipulierte Cookie-Inhalte werden von parse verworfen
eq("Muell", F.persistQuery(F.parse("<script>=1&f.gibtsnicht=x&sort=evil")), "");
console.log(failed ? `${failed} Fehler` : "Gemerkter Filter: alle Pruefungen bestanden");
process.exit(failed ? 1 : 0);
