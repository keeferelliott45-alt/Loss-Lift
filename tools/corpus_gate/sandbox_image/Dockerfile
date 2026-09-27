# The container each revision's collector runs in on the cloud corpus gate.
# Built from trusted main before any corpus or secret is on the runner, with
# main's requirements.txt. The collector runs with --network none, so a
# revision needing a package main does not install fails with exit 4.
FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /opt/losslift/requirements.txt
RUN pip install --no-compile -r /opt/losslift/requirements.txt \
    && rm -rf /root/.cache
USER 65534:65534
