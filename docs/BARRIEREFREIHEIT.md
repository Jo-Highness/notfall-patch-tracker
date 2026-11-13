# Barrierefreiheit

Stand: Version 0.4.0, 2026-11-13 (NPT-16, DECISIONS P1–P6).

## Maßstab

- **BITV 2.0** (öffentliche Stellen des Bundes) bzw. die Landesverordnungen. Beide verweisen
  auf **EN 301 549 v3.2.1, Kapitel 9 = WCAG 2.1, Stufen A und AA**. Für Unternehmen gilt nach
  dem BFSG derselbe technische Maßstab.
- Geprüft wurden alle Oberflächen:
  - Bearbeiter: Hauptliste, Übersicht/Pivot, News, FAQ, Hilfe, Fehlerseiten
  - Admin-Bereich: alle Seiten einschließlich Upload-Vorschauen, Editoren und Einstellungen
  - Erstinbetriebnahme (`/einrichtung`)
- Die Kriterien, die erst mit WCAG 2.2 hinzukommen, wurden mitbetrachtet. Gefordert sind sie
  nicht.

## Ergebnis

**Teilweise konform** mit WCAG 2.1 AA. Alle Befunde aus Codeprüfung und automatischem Test
sind behoben. Offen ist der manuelle Test mit Screenreadern (siehe „Bekannte
Einschränkungen“). Ist dieser Test bestanden und sind die Einschränkungen behoben oder
bewertet, kann die Aussage auf „vollständig konform“ geändert werden.

## Prüfverfahren

1. **Automatisch** mit `tests/e2e/a11y_smoke.py`, fester Teil von `scripts/run-e2e.sh`:
   - axe-core 4.10.3 mit den Regelsätzen `wcag2a`, `wcag2aa`, `wcag21a` und `wcag21aa`.
   - Geprüft werden 24 Seitenzustände, jeweils mit `prefers-color-scheme` hell und dunkel,
     also 48 Prüfläufe. Dazu gehören die Hauptliste mit geladenen Daten, eine FAQ ohne
     Treffer, die Editoren und ein Formular mit Fehlermeldung.
   - Für jede Seite außerdem:
     - Tastatur: Ist der Sprunglink das erste Tab-Ziel, ist der Fokus sichtbar, landet der
       Fokus nach dem Sprung auf `main`?
     - Reflow: Bei 320 CSS-Pixeln Breite darf die Seite nicht waagerecht scrollen.
   - `tests/e2e/setup_smoke.py` prüft den Installer in 4 Zuständen.
   - Jeder Verstoß lässt die Suite scheitern. Das ist der Regressionsschutz für spätere
     Änderungen und für Updates von Tabulator und Quill.
2. **Manuelle Codeprüfung** aller Templates, des CSS und des eigenen JavaScripts entlang der
   Erfolgskriterien. Alle Kontraste sind nach der WCAG-Formel gerechnet.
3. **Noch offen:** ein manueller Test mit NVDA + Firefox und mit VoiceOver + Safari, dazu
   200 %/400 % Zoom und Textabstände (1.4.12) im echten Browser.

## Umgesetzt (Auszug)

| Kriterium | Umsetzung |
|---|---|
| 1.3.1, 4.1.2 Namen und Struktur | Jede Seite hat genau eine h1 (die Hauptliste eine visuell verborgene), die Navigation einen Namen. Dialoge sind über ihre Überschrift benannt, der Konfliktdialog hat zusätzlich eine Beschreibung. Kopf-Filter, Zell-Editoren, Checkboxen in den Upload-Vorschauen, Fortschrittsbalken, die URL-Felder und leere Tabellenköpfe sind benannt. Die grid-Struktur von Tabulator ist gültig (`rowgroup`/`none`). Die Symbole ✔ und ! haben eine Textalternative. |
| 1.4.3 Kontrast Text | Alle Texte haben mindestens 4.5:1. Geändert wurden: Warnfarbe #8a5a00 (5.93:1), „bitte ausfüllen“ #7a4f00 (≥ 5.40:1), Platzhalter der Tabelle (5.96:1) und Text auf Primär-Knöpfen als Link (weiß auf #0b5cad, 6.6:1). |
| 1.4.11 Kontrast Bedienelemente | Rahmen der Eingabefelder #767f8f (4.04:1 auf Weiß, 3.73:1 auf dem Seitenhintergrund). Der Zustandsrahmen „wird gespeichert“ #8a6d00 hat ≥ 4.06:1, die Fokusrahmen ≥ 4.7:1, in der Kopfleiste 9.9:1. |
| 1.4.1 Farbe | Links in der Kopfleiste sind unterstrichen. Der Status steht immer auch als Text da. |
| 1.4.10 Reflow | Formularzeilen umbrechen. Breite Datentabellen scrollen in einem eigenen, per Tastatur erreichbaren Bereich. Die Tabelle der Hauptliste ist mindestens 20 rem hoch, bei Zoom scrollt dann die Seite. |
| 2.1.1 Tastatur | Spaltenköpfe sortieren mit Enter oder Leertaste (mit Ansage). Tab verlässt die Quill-Editoren. Den Hinweis auf eine Änderung durch andere bedient man während der Zellbearbeitung mit Alt+J (übernehmen) bzw. Alt+N (behalten). |
| 2.4.1 Blöcke überspringen | „Zum Inhalt springen“ auf allen Seiten, „Tabelle überspringen“ vor der Hauptliste. |
| 2.4.3 Fokus-Reihenfolge | Bei Live-Aktualisierung der Pivot-Tabelle bleibt der Fokus erhalten. Bei Formularfehlern geht der Fokus auf das erste fehlerhafte Feld bzw. auf die Fehlermeldung des Servers. |
| 2.4.7 Fokus sichtbar | Einheitlicher 2-px-Fokusrahmen, auch auf Tabulator-Zellen und im Quill-Editor (der Vendor setzt dort `outline: none`). |
| 2.2.1 Zeitbegrenzung | Das CSRF-Token gilt 24 h (Ausnahme „länger als 20 Stunden“). Eine offene Liste lässt sich einen Arbeitstag lang ohne Neuladen bearbeiten. |
| 3.1.2 Sprache von Teilen | Werkzeugleiste und Link-Tooltip von Quill sind deutsch beschriftet. |
| 3.3.1, 3.3.2 Fehler und Beschriftung | Feldfehler in „Neues System“ mit `aria-invalid` und `aria-describedby`, Sammelmeldung als `role=alert`. Die Feldhinweise stehen sichtbar am Feld, nicht nur als Tooltip. |
| 4.1.3 Statusmeldungen | Statuszeile, Zähler und Live-Status sind Live-Regionen. Neu angesagt werden „In die Zwischenablage kopiert“ und die Sortierung (globale Region `#sr-status`). |
| 2.5.8 (nur 2.2) Zielgröße | Der Knopf „Vorfilter entfernen“ ist mindestens 24 × 24 px groß und nennt das Feld. |

Bereits vorher erfüllt und unverändert:
- `lang="de"` und eindeutige Seitentitel
- native `<dialog>` mit Fokus-Einschluss und Esc
- `prefers-reduced-motion` für alle Hervorhebungs-Animationen
- deaktivierte Knöpfe nennen den Grund
- kein Inhalt hängt allein an der Farbe

## Bekannte Einschränkungen

1. **Virtuelles Rendering der Hauptliste.** Bei mehreren tausend Systemen stehen nur die
   sichtbaren Zeilen im DOM.
   - Im Lesemodus eines Screenreaders sind deshalb nicht alle Zeilen auf einmal erreichbar.
   - Erreichbar sind sie über Suche, Filter und Export (Excel mit allen Feldern).
   - Bewertung nach dem Screenreader-Test.
2. **Viele Tab-Stopps in der Liste.** Jede bearbeitbare Zelle ist ein Tab-Ziel, beim Fokus
   öffnet sich der Editor (Tabulator-Verhalten).
   - Abhilfe heute: der Sprunglink „Tabelle überspringen“ und die Filter.
   - Geplante Verbesserung: ein Roving-Tabindex mit Pfeiltasten.
3. **Auswahllisten von Tabulator.** Die Popups der Listen-Filter und Listen-Editoren nutzen
   keine `listbox`/`option`-Rollen.
   - Bedienbar sind sie per Tastatur (Pfeile, Enter).
   - Wie Screenreader die Auswahl ansagen, ist noch zu prüfen.
4. **Kurzhinweise an Spaltenköpfen** (Tooltip bei Maus-Hover). Derselbe Text steht in der
   Hilfe (Handbuch für Bearbeiter) und im Formular „Neues System“ sichtbar am Feld.
5. **Kein dunkles Farbschema.** Die Anwendung bleibt auch bei `prefers-color-scheme: dark`
   hell. Geprüft ist dieser Zustand mit (axe-Lauf „dark“).

## Erklärung zur Barrierefreiheit (Vorlage für Betreiber)

Öffentliche Stellen müssen eine Erklärung zur Barrierefreiheit veröffentlichen, mit
Feedback-Möglichkeit und Hinweis auf die Schlichtungsstelle (§ 12b BGG, § 7 BITV 2.0). Den
Text stellt der Betreiber bereit und verlinkt ihn, zum Beispiel aus den News eines Projekts
oder aus dem Intranet. Die Anwendung selbst kennt keine Betreiber-Kontaktdaten.

> Diese Anwendung ist mit den Anforderungen der BITV 2.0 (EN 301 549 v3.2.1, WCAG 2.1 AA)
> **teilweise vereinbar**. Nicht barrierefrei sind: [Einschränkungen 1–4 aus
> `docs/BARRIEREFREIHEIT.md` übernehmen, soweit nach eigener Prüfung zutreffend].
> Erstellt am [Datum] auf Grundlage einer Selbstbewertung (automatischer Test mit axe-core
> und Codeprüfung). **Feedback:** [Kontakt]. **Durchsetzungsverfahren:** [Schlichtungsstelle
> nach § 16 BGG bzw. Landesrecht].

## Wartung

- Neue Seiten: in `tests/e2e/a11y_smoke.py` aufnehmen (Liste der Admin-Pfade bzw.
  `scan(...)`).
- Neue Farben: als Variable in `app.css`, mit nachgerechnetem Kontrast im Kommentar.
- Update von Tabulator oder Quill: `a11y_smoke` muss grün bleiben. Die Nachbesserungen
  stehen in `hauptliste.js` (`gridA11y`) bzw. `app.js` (`nptQuillA11y`,
  `nptQuillKeyboard`).
