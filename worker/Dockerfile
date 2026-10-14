FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY worker/pyproject.toml worker/
COPY worker/atlas worker/atlas
RUN pip install ./worker
COPY db/init.sql /app/db/init.sql
COPY config /app/config
COPY shapes /app/shapes
VOLUME ["/data"]
CMD ["atlas", "run"]
