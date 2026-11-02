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
