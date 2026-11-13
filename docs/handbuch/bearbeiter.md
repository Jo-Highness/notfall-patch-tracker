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
- **Ihr Filter wird gemerkt:** Suche, Spaltenfilter und Sortierung gelten auch nach dem
  Neuladen und in **allen anderen Projekten** weiter. Filtern Sie also einmal nach Ihrem
  Namen, sehen Sie in jedem Projekt gleich Ihre Systeme. Die Zeile unter der Werkzeugleiste
  meldet dann „Gemerkter Filter angewendet“.
  - Ein Link, der selbst Filter enthält (z. B. aus einer Mail mit „Ihre Systeme“), hat
    Vorrang und ändert Ihren gemerkten Filter nicht.
  - „Filter zurücksetzen“ hebt den gemerkten Filter auf.
  - Gespeichert wird nur in Ihrem Browser (Cookie `npt_filter`, ein Jahr gültig), nicht auf
    dem Server. An einem gemeinsam genutzten Rechner vor dem Gehen „Filter zurücksetzen“
    wählen, wenn der Filter Ihren Namen enthält.

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

## Excel: Liste als Datenquelle (Pivot, automatisch aktualisieren)

Excel kann die Liste direkt als **externe Datenquelle** einbinden (Power Query). Darauf
lassen sich Pivot-Tabellen und Diagramme bauen, die sich selbst aktualisieren – ohne
jedes Mal neu zu exportieren. Beschrieben für Excel unter Windows (Microsoft 365 bzw.
2016 oder neuer).

1. **Daten → Aus dem Web** wählen und diese Adresse eintragen:
   `{{export_url}}`
   Fragt Excel nach dem Zugriff, **Anonym** wählen und **Verbinden**.
2. Im Navigator das Blatt **Systeme** markieren und **Laden in … → PivotTable-Bericht**
   wählen (oder „Tabelle“, wenn Sie die Daten zusätzlich als Tabelle sehen möchten).
3. **Automatisch aktualisieren:** **Daten → Abfragen und Verbindungen**, Rechtsklick auf
   die Abfrage → **Eigenschaften** → **Aktualisieren alle 5 Minuten** und **Daten beim
   Öffnen der Datei aktualisieren** anhaken. Sofort aktualisieren: **Daten → Alle
   aktualisieren**.

Nur einen Ausschnitt einbinden (z. B. nur Ihre Systeme): Liste filtern, dann den Knopf
**Export (Sicht)** mit der rechten Maustaste anklicken, „Link kopieren“ und diesen Link in
Schritt 1 verwenden. Er enthält Ihre Filter.

Für die Power-Query-Formel (Erweiterter Editor):

```
let
    Quelle = Excel.Workbook(Web.Contents("{{export_url}}"), null, true),
    Systeme = Quelle{[Item = "Systeme", Kind = "Sheet"]}[Data],
    Kopf = Table.PromoteHeaders(Systeme, [PromoteAllScalars = true])
in
    Kopf
```

**Wichtig:**
- Die Arbeitsmappe speichert die Adresse mit dem Projekt-Link. Wer die Datei bekommt, hat
  damit Zugang zur Liste. Vor dem Weitergeben die Abfrage löschen oder nur Werte
  weitergeben.
- Erzeugt der Admin einen neuen Link, die Adresse unter **Daten → Daten abrufen →
  Datenquelleneinstellungen** ersetzen.
- Kürzer als alle 5 Minuten bitte nicht aktualisieren: Die Daten ändern sich ohnehin nur,
  wenn jemand etwas einträgt, und viele Arbeitsmappen gleichzeitig belasten den Server.

## News und FAQ

Unter **News** veröffentlichen die Admins Meldungen zum Projekt (neueste zuerst, 20 je
Seite; jeder Beitrag hat einen eigenen Link zum Weitergeben). Unter **FAQ** stehen häufige
Fragen mit Antworten: Das Suchfeld filtert schon beim Tippen – jedes eingegebene Wort muss in
Frage oder Antwort vorkommen, Groß-/Kleinschreibung egal. News und FAQ können nur Admins
ändern.

## Zugriff per KI-Assistent (MCP)

Die Liste lässt sich auch mit einem KI-Assistenten lesen und bearbeiten, der das Model
Context Protocol (MCP, „Streamable HTTP“) beherrscht, z. B. Claude Code, GitHub Copilot
(VS Code) oder Kiro.

Adresse mit Projekt-Link (am einfachsten): `{{mcp_url}}`

Adresse ohne Token (empfohlen): `{{basis_url}}/mcp`, dazu der Header
`Authorization: Bearer <Token>`. Der Token ist der letzte Teil des Projekt-Links nach
`/p/`. So steht er weder in Proxy-Logs noch in Konfigurationsdateien.

**Claude Code** (Terminal):

```
claude mcp add --transport http npt {{mcp_url}}
```

oder mit Header:

```
claude mcp add --transport http npt {{basis_url}}/mcp --header "Authorization: Bearer <Token>"
```

**GitHub Copilot in VS Code**: Datei `.vscode/mcp.json` im Arbeitsordner (oder über
„MCP: Open User Configuration“ für alle Ordner). VS Code fragt den Token beim ersten Start
ab und speichert ihn im Geheimspeicher, nicht in der Datei:

```
{
  "inputs": [
    {
      "type": "promptString",
      "id": "npt-token",
      "description": "Token aus dem Projekt-Link (Teil nach /p/)",
      "password": true
    }
  ],
  "servers": {
    "npt": {
      "type": "http",
      "url": "{{basis_url}}/mcp",
      "headers": { "Authorization": "Bearer ${input:npt-token}" }
    }
  }
}
```

Danach im Copilot-Chat den **Agent-Modus** wählen; die Werkzeuge von „npt“ erscheinen
in der Werkzeugliste.

**Kiro**: Datei `.kiro/settings/mcp.json` im Arbeitsordner oder `~/.kiro/settings/mcp.json`
für alle Arbeitsbereiche. Den Token als Umgebungsvariable `NPT_TOKEN` setzen, dann steht
er nicht in der Datei:

```
{
  "mcpServers": {
    "npt": {
      "url": "{{basis_url}}/mcp",
      "headers": { "Authorization": "Bearer ${NPT_TOKEN}" }
    }
  }
}
```

Das Geheimnis ist derselbe Token wie im Projekt-Link – wer die MCP-Adresse hat, kann die
Liste genauso ausfüllen wie mit dem Link. Der MCP-Zugang dient nur dem **Ausfüllen der
Liste**: Der Assistent kann alle Einträge mit allen Details auflisten, Einträge durchsuchen
(Freitext, „System betroffen“/„Ausgeführte Aktion“ inkl. „noch leer“, „Feld enthält“) und
nur die vier *-Felder setzen (System betroffen, installierte Version, Ausgeführte Aktion,
Bemerkung). Systeme anlegen oder Stammdaten ändern geht per MCP nicht. Änderungen erscheinen sofort in allen
geöffneten Listen und im Änderungsprotokoll mit der Quelle „MCP“. Gleichzeitige Änderungen
schützt derselbe Konfliktmechanismus wie in der Liste.

**Achtung:** Bemerkungen und andere Freitexte stammen von anderen Bearbeitern. Ein
Assistent soll sie als Daten behandeln, nicht als Anweisungen – Änderungen des Assistenten
vor dem Bestätigen prüfen. „Token neu generieren“ durch den Admin sperrt auch den
MCP-Zugang.

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
