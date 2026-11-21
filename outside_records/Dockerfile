# Outside records: read-only title, payment and contract lookups (SPEC 6).
# Build context: outside_records. Runs as the read-only app role; every value
# (DATABASE_URL, PORT, LOG_LEVEL) comes from the environment at run time and no
# secret is in any image layer. Migrations and seeding run as one-shot commands:
#   docker run ... onepledge-outside-records db migrate
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BIND_HOST=0.0.0.0 \
    PORT=8200

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir . \
    && useradd --system --uid 10001 --no-create-home onepledge

USER onepledge
EXPOSE 8200
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'] + '/healthz', timeout=2)"

ENTRYPOINT ["onepledge-outside"]
CMD ["serve"]
