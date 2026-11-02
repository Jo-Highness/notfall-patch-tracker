/* Filter-Grammatik der Hauptliste – Browser-Gegenstück zu npt/domain/filters.py.
 * Maßgeblich ist die Python-Implementierung (Spezifikation dort im Modul-Docstring);
 * beide laufen gegen tests/vectors/filter-vectors.json. Kein Inline-JS, keine Abhängigkeiten.
 * Nutzbar im Browser (window.NptFilter) und in Node (module.exports) für die Testvektoren.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) { module.exports = api; } else { root.NptFilter = api; }
}(this, function () {
  "use strict";

  var EMPTY_KEY = "leer";
  var MAX_VALUES_PER_FIELD = 5000;
  var MAX_TEXT_LEN = 500;
  var LIST_FIELDS = ["system_id", "system_name", "sbf", "digital_access_zone", "affected",
    "installed_version", "action_taken", "remark", "system_description", "app_owner",
    "app_type", "lifecycle", "fqdn", "cio_unit", "foreign_key", "origin"];
  var ENUM_FIELDS = {
    affected: { ja: "Ja", nein: "Nein", in_pruefung: "In Prüfung" },
    action_taken: {
      update_installiert: "Update installiert", mitigiert: "Mitigiert (siehe Bemerkung)",
      isoliert: "Isoliert", abgeschaltet: "Abgeschaltet"
    },
    origin: { stammdaten: "Stammdaten", manuell_angelegt: "Manuell angelegt", admin_upload: "Admin-Upload" }
  };

  function has(obj, key) { return Object.prototype.hasOwnProperty.call(obj, key); }
  function isEnum(name) { return has(ENUM_FIELDS, name); }
  function isList(name) { return LIST_FIELDS.indexOf(name) >= 0; }
  function isText(name) { return isList(name) && !isEnum(name); }
  // Python str.strip() entfernt Unicode-Whitespace; String.prototype.trim ebenso.
  function strip(s) { return String(s).trim(); }

  function emptyState() {
    return { q: null, contains: {}, enums: {}, exact: {}, sort: null, desc: false };
  }

  /** params: Array von [key, value] (Reihenfolge erhalten) oder Query-String. */
  function parse(params) {
    var entries = params;
    if (typeof params === "string") {
      entries = [];
      new URLSearchParams(params).forEach(function (v, k) { entries.push([k, v]); });
    }
    var st = emptyState();
    var enumValues = {}, exact = {};
    var seenQ = false, seenSort = false, seenDir = false, seenContains = {};
    entries.forEach(function (pair) {
      var key = pair[0], value = String(pair[1]).slice(0, MAX_TEXT_LEN * 4), name, v;
      if (key === "q") {
        if (!seenQ) { seenQ = true; st.q = strip(value).slice(0, MAX_TEXT_LEN) || null; }
      } else if (key === "sort") {
        if (!seenSort) { seenSort = true; st.sort = isList(value) ? value : null; }
      } else if (key === "dir") {
        if (!seenDir) { seenDir = true; st.desc = value === "desc"; }
      } else if (key.slice(0, 2) === "f.") {
        name = key.slice(2);
        if (isEnum(name)) {
          v = strip(value);
          if (v) { (enumValues[name] = enumValues[name] || []).push(v); }
        } else if (isText(name) && !has(seenContains, name)) {
          seenContains[name] = true;
          v = strip(value).slice(0, MAX_TEXT_LEN);
          if (v) { st.contains[name] = v; }
        }
      } else if (key.slice(0, 2) === "x.") {
        name = key.slice(2);
        if (isEnum(name)) {
          (enumValues[name] = enumValues[name] || []).push(strip(value) || EMPTY_KEY);
        } else if (isText(name)) {
          (exact[name] = exact[name] || []).push(value);
        }
      }
    });
    Object.keys(enumValues).forEach(function (k) {
      var set = {};
      enumValues[k].slice(0, MAX_VALUES_PER_FIELD).forEach(function (x) { set[x] = true; });
      st.enums[k] = set;
    });
    Object.keys(exact).forEach(function (k) {
      var seen = {}, out = [];
      exact[k].slice(0, MAX_VALUES_PER_FIELD).forEach(function (x) {
        if (!has(seen, x)) { seen[x] = true; out.push(x); }
      });
      st.exact[k] = out;
    });
    st.desc = st.desc && st.sort !== null;
    return st;
  }

  /** Kanonische Parameterliste (gleiche Reihenfolge wie Python to_params). */
  function toParams(st) {
    var out = [];
    if (st.q) { out.push(["q", st.q]); }
    LIST_FIELDS.forEach(function (name) {
      if (has(st.contains, name)) { out.push(["f." + name, st.contains[name]]); }
      if (has(st.enums, name)) {
        Object.keys(st.enums[name]).sort(codepointCompare).forEach(function (v) { out.push(["f." + name, v]); });
      }
    });
    LIST_FIELDS.forEach(function (name) {
      if (has(st.exact, name)) { st.exact[name].forEach(function (v) { out.push(["x." + name, v]); }); }
    });
    if (st.sort) {
      out.push(["sort", st.sort]);
      if (st.desc) { out.push(["dir", "desc"]); }
    }
    return out;
  }

  function toQuery(st) {
    var usp = new URLSearchParams();
    toParams(st).forEach(function (p) { usp.append(p[0], p[1]); });
    return usp.toString();
  }

  function displayText(row, name) {
    var value = row[name];
    if (value === null || value === undefined || value === "") { return ""; }
    if (isEnum(name)) { return has(ENUM_FIELDS[name], value) ? ENUM_FIELDS[name][value] : String(value); }
    return String(value);
  }

  function enumHit(row, name, wanted) {
    var value = row[name];
    var key = (value === null || value === undefined || value === "") ? EMPTY_KEY : String(value);
    return has(wanted, key);
  }

  function matchesGlobal(row, q) {
    if (!q) { return true; }
    var needle = q.toLowerCase();
    for (var i = 0; i < LIST_FIELDS.length; i++) {
      if (displayText(row, LIST_FIELDS[i]).toLowerCase().indexOf(needle) >= 0) { return true; }
    }
    return false;
  }

  function matchesContains(row, name, text) {
    return displayText(row, name).toLowerCase().indexOf(text.toLowerCase()) >= 0;
  }

  function matchesExact(row, name, values) {
    var value = row[name];
    return values.indexOf(value === null || value === undefined ? "" : value) >= 0;
  }

  function matches(row, st) {
    var k;
    if (st.q && !matchesGlobal(row, st.q)) { return false; }
    for (k in st.contains) { if (has(st.contains, k) && !matchesContains(row, k, st.contains[k])) { return false; } }
    for (k in st.enums) { if (has(st.enums, k) && !enumHit(row, k, st.enums[k])) { return false; } }
    for (k in st.exact) { if (has(st.exact, k) && !matchesExact(row, k, st.exact[k])) { return false; } }
    return true;
  }

  // Vergleich nach Unicode-Codepoints (wie Python), nicht nach UTF-16-Einheiten.
  function codepointCompare(a, b) {
    var ia = 0, ib = 0;
    while (ia < a.length && ib < b.length) {
      var ca = a.codePointAt(ia), cb = b.codePointAt(ib);
      if (ca !== cb) { return ca < cb ? -1 : 1; }
      ia += ca > 0xffff ? 2 : 1;
      ib += cb > 0xffff ? 2 : 1;
    }
    if (ia < a.length) { return 1; }
    if (ib < b.length) { return -1; }
    return 0;
  }

  /** Aufsteigender Vergleich zweier Zeilen nach Feld (leer zuletzt, dann System-ID). */
  function compareRows(a, b, name) {
    var ta = displayText(a, name).toLowerCase(), tb = displayText(b, name).toLowerCase();
    var ea = ta === "" ? 1 : 0, eb = tb === "" ? 1 : 0;
    if (ea !== eb) { return ea - eb; }
    var c = codepointCompare(ta, tb);
    if (c !== 0) { return c; }
    return codepointCompare(String(a.system_id || ""), String(b.system_id || ""));
  }

  function apply(rows, st) {
    var name = st.sort || "system_id";
    var hit = rows.filter(function (r) { return matches(r, st); });
    hit.sort(function (a, b) { var c = compareRows(a, b, name); return st.desc ? -c : c; });
    return hit;
  }

  return {
    EMPTY_KEY: EMPTY_KEY, LIST_FIELDS: LIST_FIELDS, ENUM_FIELDS: ENUM_FIELDS,
    parse: parse, toParams: toParams, toQuery: toQuery, emptyState: emptyState,
    displayText: displayText, matches: matches, matchesGlobal: matchesGlobal,
    matchesContains: matchesContains, matchesExact: matchesExact, enumHit: enumHit,
    compareRows: compareRows, apply: apply, isEnum: isEnum
  };
}));
