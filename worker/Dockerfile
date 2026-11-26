FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY worker/pyproject.toml worker/
COPY worker/atlas worker/atlas
RUN pip install ./worker
# shacl-report (https://github.com/Matdata-eu/shacl-report) renders each data quality
# report as a self-contained HTML viewer; without it only the Turtle reports are made.
COPY --from=ghcr.io/matdata-eu/shacl-report:1.1.0 /usr/local/bin/shacl-report /usr/local/bin/shacl-report
# Its licence (Apache-2.0, which requires it to ship with the binary) and the notices of
# the third-party code compiled into it, as the shacl-report image provides them.
COPY --from=ghcr.io/matdata-eu/shacl-report:1.1.0 /usr/share/doc/shacl-report/ /usr/share/licenses/shacl-report/
# TripleTrail (https://github.com/Matdata-eu/tripletrail) validates each dataset with
# `tripletrail validate` (quality.engine auto picks it when the command is on the PATH).
# Its licence and notices (Apache-2.0) ship with the binary, as the image provides them.
COPY --from=ghcr.io/matdata-eu/tripletrail:0.1.0 /usr/local/bin/tripletrail /usr/local/bin/tripletrail
COPY --from=ghcr.io/matdata-eu/tripletrail:0.1.0 /usr/share/doc/tripletrail/ /usr/share/licenses/tripletrail/
COPY db/init.sql /app/db/init.sql
COPY config /app/config
COPY shapes /app/shapes
VOLUME ["/data"]
CMD ["atlas", "run"]
