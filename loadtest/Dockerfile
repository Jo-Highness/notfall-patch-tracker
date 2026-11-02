# Lasttest-Werkzeug (nur Test, nie ausgeliefert):
#   docker build -t npt-loadtest loadtest
#   docker run --rm --network host -e NPT_PROJECT_PATH=/p/<token> -v "$PWD/loadtest:/lt" npt-loadtest \
#     -f /lt/locustfile.py --headless -u 500 -r 25 -t 3m --reset-stats --host http://127.0.0.1:8480
FROM python:3.12.16-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
USER 1000:1000
WORKDIR /tmp
ENTRYPOINT ["locust"]
