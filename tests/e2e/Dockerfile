# syntax=docker/dockerfile:1.7
# Playwright-Laufzeit fuer die E2E-Rauchtests (tests/e2e/*.py), nur Test, nie ausgeliefert.
# Basis: Playwright-Python-Image mit Chromium-Build 1129 (passend zu playwright 1.46.0).
#   docker build -t npt-e2e tests/e2e
#   docker run --rm --network host -v "$PWD:/w:ro" -w /w npt-e2e python -m tests.e2e.live_smoke --help
FROM mcr.microsoft.com/playwright/python@sha256:98e88016a5705def757564f70469e80161c4e4dbf787bbe35a710fabb70bb7da
RUN pip install --no-cache-dir playwright==1.46.0 openpyxl==3.1.5
# axe-core (MPL-2.0) fuer tests/e2e/a11y_smoke.py (NPT-16); Pruefsumme der npm-Tarball-Datei
ADD --checksum=sha256:0f2b4d7dcdf7d1219df8d1959ad68e565f51d14c3f0d88bb71cd59abeb956292 \
    https://registry.npmjs.org/axe-core/-/axe-core-4.10.3.tgz /tmp/axe-core.tgz
RUN mkdir -p /opt/axe && tar -xzf /tmp/axe-core.tgz -C /opt/axe --strip-components=1 \
      package/axe.min.js package/LICENSE && rm /tmp/axe-core.tgz && chmod -R a+rX /opt/axe
USER pwuser
