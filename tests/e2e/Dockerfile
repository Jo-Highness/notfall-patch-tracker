# Playwright-Laufzeit fuer die E2E-Rauchtests (tests/e2e/*.py), nur Test, nie ausgeliefert.
# Basis: Playwright-Python-Image mit Chromium-Build 1129 (passend zu playwright 1.46.0).
#   docker build -t npt-e2e tests/e2e
#   docker run --rm --network host -v "$PWD:/w:ro" -w /w npt-e2e python -m tests.e2e.live_smoke --help
FROM mcr.microsoft.com/playwright/python@sha256:98e88016a5705def757564f70469e80161c4e4dbf787bbe35a710fabb70bb7da
RUN pip install --no-cache-dir playwright==1.46.0 openpyxl==3.1.5
USER pwuser
