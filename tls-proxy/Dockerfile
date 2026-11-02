# public-tls: TLS-Proxy vor dem Bearbeiter-Port (Overlay docker-compose.tls.yml, DECISIONS J14).
# Basisabbild per Digest, gleiche Version wie das admin-gateway (gateway/Dockerfile, K9).
FROM nginx:1.31.8-alpine@sha256:785835ea65db4bba6f021acc0f3be91b0fef76a53fe41f8588c6698d2aed0606

# Verhalten wie das offizielle Abbild: Standard-Entrypoint mit envsubst der Templates aus
# /etc/nginx/templates; Konfiguration und Zertifikate kommen als Read-only-Mounts.
# Nur ungenutzte Module und curl entfernen (offline, reproduzierbar, kein apk upgrade):
# sie ziehen brotli, libcurl, freetype, libxml2/libxslt, GeoIP u. a. nach (SBOM-Scan, K10).
RUN apk del --no-network --quiet curl nginx-module-acme nginx-module-geoip \
      nginx-module-image-filter nginx-module-njs nginx-module-xslt

# OCI-Metadaten (J15), Build-Args wie im App-Dockerfile.
ARG NPT_VERSION=0.2.1
ARG NPT_REVISION=""
ARG NPT_SOURCE_URL=""
LABEL org.opencontainers.image.title="notfall-patch-tracker-tls-proxy" \
      org.opencontainers.image.description="TLS-Proxy (nginx) vor dem Bearbeiter-Bereich des Notfall-Patch-Trackers (Overlay docker-compose.tls.yml)" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${NPT_VERSION}" \
      org.opencontainers.image.revision="${NPT_REVISION}" \
      org.opencontainers.image.source="${NPT_SOURCE_URL}"
