# Security-Header fuer Antworten, die das Gateway selbst erzeugt (403/404/413/Redirect,
# robots.txt). Weitergeleitete Admin-Antworten bringen ihre Header von der App mit.
# nginx erbt add_header nicht in Locations mit eigenem add_header -> hier vollstaendig.
add_header Content-Security-Policy "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "no-referrer" always;
add_header X-Robots-Tag "noindex, nofollow" always;
add_header Cache-Control "no-store" always;
add_header Strict-Transport-Security "max-age=31536000" always;
