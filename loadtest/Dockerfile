# Lasttest-Werkzeug (nur Test, nie ausgeliefert):
#   docker build -t npt-loadtest loadtest
#   docker run --rm --network host -e NPT_PROJECT_PATH=/p/<token> -v "$PWD/loadtest:/lt" npt-loadtest \
#     -f /lt/locustfile.py --headless -u 500 -r 25 -t 3m --reset-stats --host http://127.0.0.1:8480
FROM python:3.12.16-slim-trixie@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
USER 1000:1000
WORKDIR /tmp
ENTRYPOINT ["locust"]
