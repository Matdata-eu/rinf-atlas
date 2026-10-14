FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY worker/pyproject.toml worker/
COPY worker/atlas worker/atlas
RUN pip install ./worker
# shacl-report (https://github.com/Matdata-eu/shacl-report) renders each data quality
# report as a self-contained HTML viewer; without it only the Turtle reports are made.
COPY --from=ghcr.io/matdata-eu/shacl-report:1.0.0 /usr/local/bin/shacl-report /usr/local/bin/shacl-report
COPY db/init.sql /app/db/init.sql
COPY config /app/config
COPY shapes /app/shapes
VOLUME ["/data"]
CMD ["atlas", "run"]
