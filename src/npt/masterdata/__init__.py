"""Stammdatenpflege (Auftrag 4): Quellen, Validierung, Diff, Uebernahme.

Ablauf fuer jede Quelle (Excel heute, API perspektivisch) identisch:
``MasterDataSource.fetch()`` -> ``validate_records`` -> Vorschau (staged) ->
Uebernahme erst nach Admin-Bestaetigung, Diff unter Sperre neu berechnet.
"""
