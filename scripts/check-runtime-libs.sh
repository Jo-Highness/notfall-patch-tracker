#!/bin/sh
# Laeuft beim Bau der Laufzeitstufe (Dockerfile, DECISIONS K14): Nach dem Entfernen
# ungenutzter Debian-Pakete muss jede dynamisch gelinkte Datei unter den angegebenen
# Verzeichnissen ihre Bibliotheken noch finden. Bewusst ohne Bibliothek bleiben nur
# Python-Module, die die App nicht nutzt:
#   _sqlite3 (libsqlite3-0 entfernt), _curses/_curses_panel (ncurses entfernt),
#   _uuid (libuuid1 entfernt; das Modul uuid faellt auf die reine Python-Umsetzung zurueck),
#   _tkinter (im slim-Abbild ohnehin ohne Tcl/Tk).
# Aufruf: check-runtime-libs.sh <verzeichnis> [<verzeichnis> ...]; Exitcode 1 bei Befund.
set -eu
allowed='/(_sqlite3|_curses|_curses_panel|_uuid|_tkinter)\.cpython-[^/]*\.so$'
report="$(mktemp)"
find "$@" -type f \( -name '*.so' -o -name '*.so.*' -o -perm -u+x \) -print | while IFS= read -r f; do
  # Kein dynamisches ELF (Skript, statisch gelinkt): ldd meldet einen Fehler -> ueberspringen.
  out="$(ldd "$f" 2>/dev/null)" || continue
  case "$out" in *"not found"*) ;; *) continue ;; esac
  if printf '%s\n' "$f" | grep -Eq "$allowed"; then continue; fi
  printf '%s\n%s\n' "FEHLT: $f" "$(printf '%s\n' "$out" | grep 'not found')" >> "$report"
done
if [ -s "$report" ]; then
  cat "$report" >&2
  rm -f "$report"
  exit 1
fi
rm -f "$report"
echo "check-runtime-libs: alle Bibliotheken vorhanden ($*)"
