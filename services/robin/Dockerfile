# R-206e (D-165): Robin (https://github.com/apurvsinghgautam/robin, MIT) as a
# separate service. Its code comes from OUR full copy (release mirror-robin of
# reeveero-tech/All-hand, refreshed by the tools-mirror workflow), so the
# service still builds if the upstream repository is archived or removed.
# Only our wrapper (app.py) is added; Robin's files are not modified.
FROM python:3.11-slim

ARG ROBIN_SRC_URL=https://github.com/reeveero-tech/All-hand/releases/download/mirror-robin/robin-src.tar.gz

RUN DEBIAN_FRONTEND=noninteractive apt-get update && \
    apt-get install -y --no-install-recommends tor curl ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --gid 1000 robin && \
    useradd --uid 1000 --gid 1000 --create-home --home-dir /home/robin robin

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Robin's code from our mirror; its SHA256 is kept in the image for /health.
RUN curl -fsSL "$ROBIN_SRC_URL" -o /tmp/robin-src.tar.gz && \
    sha256sum /tmp/robin-src.tar.gz | cut -d' ' -f1 > /app/ROBIN_SRC_SHA256 && \
    tar xzf /tmp/robin-src.tar.gz -C /app && rm /tmp/robin-src.tar.gz && \
    test -f /app/robin/pipeline.py && test -f /app/robin/search.py && \
    chown -R robin:robin /app && \
    install -d -m 0700 -o robin -g robin /home/robin/.tor /home/robin/.robin

COPY --chown=robin:robin app.py start.sh /app/
RUN chmod +x /app/start.sh

USER robin
ENV HOME=/home/robin \
    PYTHONPATH=/app/robin \
    ROBIN_TOR_LOG=/home/robin/tor-notices.log \
    ROBIN_SRC_URL=$ROBIN_SRC_URL \
    PORT=10000
EXPOSE 10000
CMD ["/app/start.sh"]
