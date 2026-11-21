# TEST DOUBLES of the layer 2 key holder and registry (not production code).
# Build context: contracts/fakes. One image serves both doubles:
#   registry:   default entrypoint (onepledge-fake-registry serve), PORT 8400
#   key holder: entrypoint ["onepledge-fake-keyholder", "serve"], PORT 8301 or 8302
# All configuration comes from the environment at run time; no secret is in the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BIND_HOST=0.0.0.0

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd --system --uid 10001 --no-create-home onepledge

USER onepledge
EXPOSE 8301 8302 8400
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'] + '/healthz', timeout=2)"

ENTRYPOINT ["onepledge-fake-registry", "serve"]
