# admin-gateway: nginx mit mTLS vor dem internen Admin-Port der App (Auftrag 8.1, NPT-06).
# Basisabbild per Digest (NPT-12 K9); gleiche Version wie public-tls (tls-proxy/Dockerfile).
FROM nginx:1.31.8-alpine@sha256:785835ea65db4bba6f021acc0f3be91b0fef76a53fe41f8588c6698d2aed0606

# Eigene Konfiguration statt des Standard-Entrypoints (envsubst mit expliziter
# Variablenliste, optionale CRL, Reload-Schleife fuer Zertifikat/CA/CRL).
# Nicht benoetigte Module und curl entfernen (offline, reproduzierbar): sie ziehen
# brotli, libcurl, freetype, libxml2/libxslt, GeoIP u. a. nach (SBOM-Scan, NPT-12).
RUN rm -f /etc/nginx/conf.d/default.conf /docker-entrypoint.d/*.sh /docker-entrypoint.d/*.envsh \
 && apk del --no-network --quiet curl nginx-module-acme nginx-module-geoip \
      nginx-module-image-filter nginx-module-njs nginx-module-xslt
COPY nginx.conf /etc/nginx/nginx.conf
COPY admin.conf.template security-headers.inc /etc/npt-gateway/
COPY entrypoint.sh crl-reload.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/entrypoint.sh /usr/local/bin/crl-reload.sh

EXPOSE 8443
STOPSIGNAL SIGQUIT
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# OCI-Metadaten (J15), Build-Args wie im App-Dockerfile.
ARG NPT_VERSION=0.2.0
ARG NPT_REVISION=""
ARG NPT_SOURCE_URL=""
LABEL org.opencontainers.image.title="notfall-patch-tracker-gateway" \
      org.opencontainers.image.description="mTLS-Gateway (nginx) vor dem Admin-Bereich des Notfall-Patch-Trackers" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${NPT_VERSION}" \
      org.opencontainers.image.revision="${NPT_REVISION}" \
      org.opencontainers.image.source="${NPT_SOURCE_URL}"
