# Admin-Handbuch

Der Admin-Bereich ist nur über das **admin-gateway mit Client-Zertifikat (mTLS)**
erreichbar ({{admin_base_url}}/admin/). Bearbeiter arbeiten ohne Anmeldung über die
Projekt-URL unter {{public_base_url}}/p/… – dort gibt es keinen Admin-Bereich.

## Ablauf „Notfall tritt ein“

1. **Stammdaten prüfen** – *Stammdaten*: Ist der letzte Import aktuell (Import-Historie)?
   Sonst zuerst die aktuelle Excel-Liste importieren (siehe unten).
2. **Projekt anlegen** – Startseite *Projekte* → *Neues Projekt*: Name (z. B.
   „CVE-2026-12345 Log4Shell“) und Beschreibung (CVE, Link auf das Advisory, Frist).
   Dabei wird eine **Kopie** der Stammdaten erzeugt; spätere Stammdatenänderungen wirken
   nicht auf das Projekt.
3. **Link verteilen** – Projektseite → *Projekt-URL für Bearbeiter* → *Kopieren*. Den Link
   an die Prüfenden geben, am besten zusätzlich per *Mailversand* an alle
   Anwendungsverantwortlichen mit ihren Systemen (Zielgruppe „System betroffen noch
   leer“, Text mit `[LINK]`).
4. **Fortschritt verfolgen** – Projektliste (Fortschritt in %), *Übersicht* des Projekts
   (Pivot, Drilldown) und *Änderungsprotokoll*. Nach einigen Stunden Erinnerungsmail an
   „System betroffen noch leer“ bzw. „Betroffen = Ja, Aktion noch leer“ senden.
   Korrekturen per Excel-Upload oder *Stammdaten bearbeiten* je System.
5. **Abschließen** – Wenn alles bewertet ist: *Status* → *Abschließen* (schreibgeschützt,
   weiter lesbar), Export ziehen, später *Archivieren*. Manuell angelegte Systeme über
   *Export „manuell angelegte Systeme“* im Quellsystem nachpflegen.

## Stammdatenimport

*Stammdaten* → *Excel-Import*:

1. *Excel-Vorlage herunterladen* zeigt die erwarteten Spalten. Pflicht ist „System-ID“;
   Groß-/Kleinschreibung und Leerzeichen in der Kopfzeile sind egal.
2. Datei (.xlsx, Grenze `UPLOAD_MAX_MB`) hochladen → **Vorschau**: Anzahl und Listen
   neuer, geänderter und **im Upload fehlender** Systeme sowie der Validierungsbericht.
   Doppelte oder leere System-IDs sperren die Übernahme; ungültige E-Mail-Adressen und
   IDs mit dem reservierten Präfix `NEW-` sind Warnungen (`NEW-`-Zeilen werden nicht
   übernommen).
3. **Übernehmen**. Fehlende Systeme bleiben standardmäßig erhalten und werden markiert;
   entfernt wird nur, was Sie ausdrücklich ankreuzen. Hat sich der Bestand seit der
   Vorschau geändert, erscheint eine aktualisierte Vorschau zur erneuten Bestätigung.

Jeder Import steht mit Datei-Hash, Admin und Zählern in der *Import-Historie*.

## Projekt anlegen und URL verteilen

- *Neues Projekt* → Name, Beschreibung → *Projekt anlegen*.
- Die **Projekt-URL** ist der einzige Zugang für Bearbeiter (256-Bit-Zufallswert). Nur an
  Berechtigte geben. Ist sie in falsche Hände geraten: *Token neu generieren* – die alte
  URL ist sofort ungültig, offene Listen zeigen „Link nicht mehr gültig“; danach den neuen
  Link erneut verteilen.
- Mehrere Projekte können parallel laufen.

## Upload ins Projekt mit Konfliktbestätigung

Projektseite → *Excel-Datei in das Projekt hochladen*:

1. Datei hochladen (Spalten wie Vorlage bzw. Export der Hauptliste, Schlüssel System-ID).
   Der Upload darf **alle** Felder überschreiben, auch Stammdatenfelder. Ungültige
   Auswahlwerte, zu lange Texte und doppelte/leere IDs sind Fehler mit Zeilenangabe.
2. **Vorschau**:
   - **Konflikte** – Feld im Projekt gefüllt und abweichend (alt/neu je Feld). Nur
     angekreuzte werden übernommen: einzeln, je Zeile, „alle Konflikte auswählen“ oder
     *Übernehmen und alle Konflikte überschreiben*.
   - **Neubefüllungen** – Feld im Projekt leer; werden übernommen.
   - **Unbekannte System-IDs** – Standard „ignorieren“; angekreuzte werden mit Herkunft
     „Admin-Upload“ angelegt.
   - **Unverändert** – nur als Anzahl.
   - **Leere Zellen löschen keine vorhandenen Werte.**
3. **Übernehmen**: Haben Bearbeiter betroffene Felder inzwischen geändert, erscheint die
   aktualisierte Vorschau – erneut prüfen und bestätigen. Sonst wird in einer Transaktion
   geschrieben; vorher entsteht automatisch ein Backup „vor Import“, alle offenen Listen
   aktualisieren sich live, jede Änderung steht im Änderungsprotokoll.

## Backups und Wiederherstellung

Projektseite → *Backups*:

- **Intervall** (aus, 15 min … 24 h; Standard 1 h) und **Aufbewahrung** (letzte n,
  Standard 48). Gesichert wird nur, wenn sich seit dem letzten Backup Daten geändert haben.
- *Backup jetzt erstellen*; automatisch zusätzlich „vor Import“ und „vor Wiederherstellung“.
- Jedes Backup als **JSON** (vollständig) und **Excel** herunterladbar.
- *Wiederherstellen …* → Bestätigung ankreuzen → *Wiederherstellen*: Alle Systeme werden
  auf den Stand des Backups gesetzt (vorher automatisches Backup „vor Wiederherstellung“),
  offene Listen laden neu. Nur in aktiven Projekten.

## Mailversand mit `[LINK]` und Zielgruppen

Projektseite → *Mailversand / Kampagnen* → *Neue Kampagne*:

1. **Betreff** (Klartextzeile) und **Text** im Editor (Überschriften, fett, kursiv, Listen,
   Links). *[LINK] einfügen* setzt den Platzhalter `[LINK]` – am besten als eigener Absatz.
   Er wird je Empfänger ersetzt durch je einen Link „System-Name (System-ID)“ auf die
   gefilterte Liste und den Sammel-Link „Alle meine Systeme“.
2. **Zielgruppe**:
   - *System betroffen noch leer* – Verantwortliche mit mindestens einem unbearbeiteten
     System; `[LINK]` enthält nur diese Systeme.
   - *Betroffen = Ja, Aktion noch leer* – entsprechend nur diese Systeme.
   - *Benutzerdefiniert* – Adresse einer gefilterten Hauptlisten-Sicht einfügen (Filter und
     Suche werden übernommen).
3. *Speichern und Vorschau*: Anzahl Empfänger (je Adresse genau eine Mail; mehrere
   Adressen im Feld, getrennt mit `;` oder `,`, ergeben mehrere Mails), Anzahl Systeme,
   Beispielmail für einen wählbaren Empfänger, **übersprungene** leere/ungültige Adressen.
4. *Testmail senden* an eine beliebige Adresse (Betreff mit „[TEST]“).
5. **Versand**: Häkchen setzen, Dialog mit der Empfängerzahl bestätigen. Versendet wird im
   Hintergrund (Standard 5 Mails/s, vorübergehende Fehler bis zu 5 Versuche). Der
   *Versandstatus* je Empfänger aktualisiert sich selbst; *Ausstehende abbrechen* ist
   möglich. Erneuter Versand nur ausdrücklich: an alle oder nur an die fehlgeschlagenen.

Ohne SMTP-Konfiguration (`SMTP_HOST`, `SMTP_FROM`) ist der Versand gesperrt.

## News und FAQ pflegen

Auf der Projektseite unter **News, FAQ und MCP-Zugang**: „News verwalten“ bzw. „FAQ
verwalten“. Beiträge und FAQ-Einträge werden mit einem Editor geschrieben (Überschriften,
fett, kursiv, Listen, Links mit http/https/mailto). Alles andere – Bilder, Skripte,
Farben – entfernt der Server beim Speichern. FAQ-Einträge haben eine Reihenfolge
(kleinere Zahl steht weiter oben; neue Einträge bekommen automatisch die nächste
Zehnerstelle). Lesen kann jeder mit der Projekt-URL, ändern nur Admins; jede Änderung
steht im Audit-Log. Pflegen ist auch in abgeschlossenen und archivierten Projekten möglich.
Höchstens 1000 Beiträge bzw. FAQ-Einträge je Projekt.

News und FAQ sind **nicht** Teil der Projekt-Backups (die sichern nur die Systemliste) –
gesichert sind sie nur über die Datenbanksicherung. Beim Löschen des Projekts werden sie
mitgelöscht.

## MCP-Zugang für KI-Assistenten

Die Projektseite zeigt die MCP-Adresse (`<Projekt-URL>/mcp`). Sie trägt denselben Token
wie die Projekt-URL – also genauso vertraulich verteilen. Empfohlen ist die Variante
`<Basis-URL>/mcp` mit Header `Authorization: Bearer <Token>`, weil der Token dann nicht
in Proxy-Logs landet. „Token neu generieren“ sperrt Projekt-URL und MCP-Zugang sofort.
Der MCP-Zugang dient nur dem Ausfüllen der Liste (auflisten, durchsuchen, die vier *-Felder
setzen) – keine Administration, keine Neuanlage von Systemen. Änderungen stehen im
Änderungsprotokoll mit der Quelle „MCP“.

## Einstellungen

*Einstellungen* (`/admin/einstellungen`):

- **Seitenname** – steht oben auf jeder Seite und im Fenstertitel, auch bei den Bearbeitern
  (spätestens nach 30 Sekunden).
- **CA für Admin-Zertifikate** – Root und ggf. Intermediates, mit denen das Gateway die
  Admin-Zertifikate prüft. Hochladen ersetzt das bisherige Bundle vollständig. Das Gateway
  übernimmt die Änderung ohne Neustart innerhalb von etwa einer Minute. Deckt die neue CA Ihr
  eigenes Zertifikat nicht ab, verlangt die Seite eine Bestätigung, dass eine per Datei
  bereitgestellte CA es weiter abdeckt – sonst würden Sie sich aussperren.
- **Sperrliste (CRL)** – für **jede** CA oben eine CRL (mehrere hintereinander in einer
  PEM-Datei), vor dem Ablaufdatum erneuern. Eine abgelaufene oder unvollständige Sperrliste
  sperrt **alle** Admins aus; die Seite lehnt unvollständige ab und fragt nach, wenn Ihr
  eigenes Zertifikat gesperrt würde. Notausgang für den Betrieb:
  `python -m npt.trust_reset`.
- Das **Gateway-Serverzertifikat** ist ohne eigenes Zertifikat ein selbstsigniertes; die
  Seite zeigt seinen Fingerprint zum Vergleich mit der Browserwarnung.

Jede Änderung steht im *Audit-Log*.

## Admins ernennen

*Admin-Benutzer*:

- **Hinzufügen** per Zertifikats-Identifier (je nach `ADMIN_CERT_ID_ATTRIBUTE` der
  Subject-DN, die E-Mail- oder UPN-Adresse oder der SHA-256-Fingerprint). Den DN eines
  Zertifikats zeigt `openssl x509 -in cert.pem -noout -subject -nameopt RFC2253`.
- **Freischaltungsanfragen**: Wer ein gültiges Zertifikat der konfigurierten CA, aber
  keine Admin-Rechte hat, sieht seinen Identifier und kann eine Freischaltung anfragen.
  Offene Anfragen hier mit *Bestätigen* oder *Ablehnen* entscheiden.
- *Deaktivieren* / *Entfernen*. Der letzte aktive Admin ist geschützt.
- Jede Admin-Aktion steht mit Identifier im *Audit-Log*.

## Projekt abschließen, archivieren, löschen

- *Abschließen*: schreibgeschützt, Link und Export funktionieren weiter; *Wieder öffnen*
  hebt das auf.
- *Archivieren*: wie abgeschlossen, in der Projektliste als archiviert markiert;
  *Archivierung aufheben* führt zurück zu „abgeschlossen“.
- *Endgültig löschen* nur nach Eingabe des exakten Projektnamens; Systeme und
  Backup-Dateien werden entfernt, das Änderungsprotokoll bleibt erhalten.
