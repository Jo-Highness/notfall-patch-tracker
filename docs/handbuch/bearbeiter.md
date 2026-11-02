# Hilfe für Bearbeiter – {{projekt}}

**Ihre Aufgabe:** Prüfen Sie für jedes System, für das Sie zuständig sind, ob es von der
Schwachstelle betroffen ist, und tragen Sie das Ergebnis direkt in die Liste ein. Eine
Anmeldung gibt es nicht – wer diesen Link kennt, kann mitarbeiten. Geben Sie ihn daher
nur an Kolleginnen und Kollegen weiter, die mitprüfen sollen.

Projekt-Link: `{{projekt_url}}`

## Die vier Felder mit *

Nur diese Felder können Sie bearbeiten; alle anderen Spalten stammen aus den Stammdaten.
In der Liste sind sie **gelb hinterlegt** und im Spaltenkopf mit ✎ markiert; ist
„System betroffen“ oder „Ausgeführte Aktion“ noch leer, steht dort „bitte ausfüllen“.

| Feld | Bedeutung | Auswahl / Format |
|---|---|---|
| System betroffen* | Ist das System betroffen? | **Ja**, **Nein**, **In Prüfung** (Prüfung läuft noch); leer = noch nicht bearbeitet |
| Installierte Version* | Installierte Version der betroffenen Komponente | eine Zeile, z. B. `2.17.1` |
| Ausgeführte Aktion* | Was wurde getan? | **Update installiert**, **Mitigiert (siehe Bemerkung)**, **Isoliert**, **Abgeschaltet**; leer = noch nichts |
| Bemerkung* | Freitext für Details | z. B. Mitigation, geplanter Termin, Ansprechpartner (max. 4000 Zeichen) |

Faustregel: Bei **Ja** auch eine **Ausgeführte Aktion** eintragen; bei „Mitigiert“ in der
Bemerkung kurz beschreiben, wie.

## Bearbeiten in der Liste

- In die Zelle klicken: Auswahlfelder öffnen eine Liste und **speichern sofort**;
  Textfelder speichern, sobald Sie das Feld verlassen (Klick daneben, Tab oder Enter).
- Ein **grüner Rahmen** heißt „gespeichert“, **rot** heißt „Fehler“ (die Meldung steht über
  der Liste – einfach erneut versuchen).
- Beim ersten Speichern werden Sie optional nach Name oder Kürzel gefragt. Er wird zu Ihren
  Änderungen protokolliert und in diesem Browser gemerkt; „Überspringen“ ist erlaubt.
- Änderungen anderer erscheinen **ohne Neuladen** und blinken kurz auf. Oben rechts zeigt
  „Live“ die Verbindung; „Abgleich alle 5 s“ bedeutet, dass die Live-Verbindung gerade
  fehlt – Ihre Liste wird trotzdem aktuell gehalten.
- Ist das Projekt **abgeschlossen**, ist die Liste nur noch lesbar (Hinweis oben).

## Filtern und Suchen

- Unter jeder Spaltenüberschrift gibt es einen Filter: bei Auswahlfeldern Mehrfachauswahl
  (inkl. „(leer)“), bei Textspalten „enthält …“. Ein Klick auf die Überschrift sortiert.
- Das Suchfeld oben durchsucht **alle Spalten**.
- Filter und Suche stehen in der Adresszeile: Den Link können Sie weitergeben, er zeigt
  genau diese Sicht. „Filter zurücksetzen“ zeigt wieder alles.
- „x von y Systemen angezeigt“ zählt die aktuelle Sicht.

## Wenn jemand anderes gleichzeitig ändert (Konflikt)

- **Konfliktmeldung beim Speichern:** Hat jemand das Feld geändert, seit Sie die Liste
  geladen haben, erscheint „Zwischenzeitlich geändert“ mit dem aktuellen und Ihrem Wert.
  Wählen Sie **Aktuellen Wert übernehmen** (Ihre Eingabe verwerfen) oder **Trotzdem
  überschreiben** (Ihr Wert gilt). Nichts wird still überschrieben.
- **Hinweis während des Tippens:** Ändert jemand das Feld, in dem Sie gerade schreiben,
  bleibt Ihr Text stehen. Der Hinweis „Wert wurde von anderer Person geändert“ bietet
  **Übernehmen** (fremden Wert anzeigen) oder **Behalten** (Ihr Wert wird beim Verlassen
  gespeichert).
- Änderungen an **verschiedenen** Feldern derselben Zeile sind kein Konflikt.
- „Ansicht aktualisieren“ erscheint, wenn fremde Änderungen Ihre Filter oder Sortierung
  betreffen – die Liste springt nicht von selbst, damit Ihnen keine Zeile wegrutscht.

## Neues System anlegen

Fehlt ein System in der Liste, klicken Sie **Neues System anlegen**. Alle
Stammdatenfelder sind Pflicht (beim Anwendungsverantwortlichen eine gültige
E-Mail-Adresse); für Zone, SBF usw. werden vorhandene Werte vorgeschlagen. Die System-ID
(`NEW-0001`, `NEW-0002` …) wird automatisch vergeben. Danach können nur noch die
*-Felder geändert werden – Korrekturen der Stammdaten übernimmt der Admin.

## Übersicht und Drilldown

[Übersicht](uebersicht) (Link „Übersicht“ oben) zählt die Systeme je **Digital Access Zone**
(Zeilen) und **System betroffen** (Spalten), mit Summen. Darüber stehen der Anteil
bearbeiteter Systeme und die Zahl „Ja ohne Aktion“. **Jede Zahl ist ein Link** und öffnet
die Liste genau mit diesen Systemen. Die Übersicht aktualisiert sich selbst.

## Excel-Export

**Export (Sicht)** lädt die aktuell gefilterte Liste, **Export (alle)** alle Systeme des
Projekts als .xlsx (Kopfzeile fixiert, Autofilter gesetzt).

## FAQ

**Muss ich mich anmelden?** Nein. Der Link ist der Zugang – nicht öffentlich teilen.

**Wird mein Eintrag gespeichert, wenn ich den Tab schließe?** Auswahlfelder sofort,
Textfelder beim Verlassen des Felds. Warten Sie auf den grünen Rahmen.

**Ich sehe „Link nicht mehr gültig“.** Der Admin hat einen neuen Link erzeugt oder das
Projekt gelöscht. Fragen Sie nach dem aktuellen Link.

**Ich kann nichts mehr ändern.** Das Projekt ist abgeschlossen oder archiviert
(schreibgeschützt). Lesen und Export funktionieren weiter.

**Ein Stammdatenfeld (Name, Verantwortlicher …) ist falsch.** Bitte dem Admin melden; in
der Liste sind diese Felder bewusst schreibgeschützt.

**„Zu viele Änderungen in kurzer Zeit“?** Je Client-IP-Adresse sind etwa 30
Speichervorgänge je 10 Sekunden erlaubt – kurz warten, dann wird automatisch erneut
gespeichert. Achtung: Arbeiten mehrere Personen hinter derselben Adresse (z. B. über NAT
oder einen Proxy, den der Admin nicht als vertrauenswürdig eingetragen hat – Einstellung
`TRUSTED_PROXY_IPS`), teilen sie sich dieses Kontingent. Tritt die Meldung häufig auf,
bitte dem Admin Bescheid geben.

[Zurück zur Liste](./)
