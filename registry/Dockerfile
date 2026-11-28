# OnePledge registry (SPEC section 3). Build context: registry.
# Every value (PORT, BIND_HOST) comes from the environment at run time and no secret is
# in any image layer. For example:
#   docker run -p 8400:8400 IMAGE
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BIND_HOST=0.0.0.0 \
    PORT=8400

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir . \
    && useradd --system --uid 10001 --no-create-home onepledge

USER onepledge
EXPOSE 8400
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'] + '/healthz', timeout=2)"

ENTRYPOINT ["onepledge-registry"]
CMD ["serve"]
