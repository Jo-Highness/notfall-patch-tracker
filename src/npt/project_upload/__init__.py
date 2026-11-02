"""Admin-Upload in ein Projekt (Auftrag 6.1, NPT-04).

Ablauf: .xlsx parsen und pruefen (``parse``) -> in ``staged_uploads`` ablegen ->
Diff-Vorschau mit Fingerprint (``diff``) -> Bestaetigung: unter Projektsperre neu
rechnen, bei Abweichung neue Vorschau, sonst Backup ``vor_import`` und Schreiben in
einer Transaktion (``service``).
"""
