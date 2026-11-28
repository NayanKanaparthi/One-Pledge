# OnePledge key holder (SPEC section 3). Build context: key_holder.
# One image serves both holders; HOLDER_ID, ACTIVE_KEY_VERSION and PORT come from the
# environment at run time, and no secret is in any image layer. For example:
#   docker run -e HOLDER_ID=kh1 -e ACTIVE_KEY_VERSION=1 -e PORT=8301 -p 8301:8301 IMAGE
#   docker run -e HOLDER_ID=kh2 -e ACTIVE_KEY_VERSION=1 -e PORT=8302 -p 8302:8302 IMAGE
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BIND_HOST=0.0.0.0 \
    PORT=8301

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir . \
    && useradd --system --uid 10001 --no-create-home onepledge

USER onepledge
EXPOSE 8301 8302
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'] + '/healthz', timeout=2)"

ENTRYPOINT ["onepledge-keyholder"]
CMD ["serve"]
