# Wegwerf-Abbild fuer den helm-rollback-Test: App-Abbild + eine zusaetzliche Migration.
#   docker build -t notfall-patch-tracker:k8s-mig --build-arg BASE=notfall-patch-tracker:k8s tests/k8s/rollback
ARG BASE=notfall-patch-tracker:k8s
FROM ${BASE}
COPY rollback_migration.py /opt/venv/lib/python3.12/site-packages/npt/migrations/versions/0004_k8s_rollback_test.py
