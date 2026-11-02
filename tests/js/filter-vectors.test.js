// Prueft static/js/npt-filter.js gegen die gemeinsamen Testvektoren (NPT-01).
// Aufruf: node tests/js/filter-vectors.test.js   (Exitcode 0 = alle bestanden)
"use strict";
const path = require("path");
const fs = require("fs");
const F = require(path.join(__dirname, "..", "..", "src", "npt", "static", "js", "npt-filter.js"));
const data = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "vectors", "filter-vectors.json"), "utf8"));
let failed = 0;
for (const c of data.cases) {
  const st = F.parse(c.query);
  const got = F.apply(data.rows, st).map((r) => r.system_id);
  const okIds = JSON.stringify(got) === JSON.stringify(c.expect);
  const canon = F.toQuery(st);
  const okCanon = c.canonical === undefined || canon === c.canonical;
  if (!okIds || !okCanon) {
    failed++;
    console.log(`FEHLER ${c.name}: ids=${JSON.stringify(got)} canonical=${canon}`);
  }
  // Rundlauf: kanonische Query ergibt dieselbe Sicht
  const again = F.apply(data.rows, F.parse(canon)).map((r) => r.system_id);
  if (JSON.stringify(again) !== JSON.stringify(got)) {
    failed++;
    console.log(`FEHLER Rundlauf ${c.name}`);
  }
}
console.log(`${data.cases.length - failed}/${data.cases.length} JS-Filtervektoren bestanden`);
process.exit(failed ? 1 : 0);
