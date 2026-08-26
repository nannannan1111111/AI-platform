ARG PYTHON_BASE_IMAGE=python:3.12-alpine@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31
ARG NODE_BASE_IMAGE=node:24.19.0-alpine@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43

FROM ${NODE_BASE_IMAGE} AS frontend-builder

WORKDIR /build/frontend/admin

COPY frontend/admin/package.json frontend/admin/package-lock.json ./
RUN npm ci
COPY frontend/admin/ ./
RUN npm run build

FROM ${PYTHON_BASE_IMAGE} AS python-dependencies

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

COPY backend/requirements.lock requirements.lock
RUN python -m pip install \
        --no-cache-dir \
        --disable-pip-version-check \
        --require-hashes \
        --only-binary=:all: \
        --no-compile \
        --prefix=/install \
        -r requirements.lock

FROM ${PYTHON_BASE_IMAGE} AS runtime

ARG BUILD_CREATED=1970-01-01T00:00:00Z
ARG SOURCE_URL=unknown
ARG VCS_REF=unknown
ARG VERSION=0.0.0-local

LABEL org.opencontainers.image.created=${BUILD_CREATED} \
      org.opencontainers.image.description="Creative Studio SaaS backend and generation worker" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.revision=${VCS_REF} \
      org.opencontainers.image.source=${SOURCE_URL} \
      org.opencontainers.image.title="Creative Studio SaaS" \
      org.opencontainers.image.version=${VERSION}

# Refresh the locked Alpine runtime's security libraries at image build time.
RUN apk upgrade --no-cache libcrypto3 libssl3

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend

WORKDIR /app

RUN addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app app \
    && mkdir -p /var/lib/infinite-canvas/generated-media /var/lib/infinite-canvas/provider-secrets \
    && chown -R app:app /var/lib/infinite-canvas

COPY --from=python-dependencies /install/ /usr/local/
COPY backend/app backend/app
COPY --from=frontend-builder /build/backend/app/webui/static/admin-vue backend/app/webui/static/admin-vue

COPY backend/alembic backend/alembic
COPY backend/alembic.ini backend/alembic.ini
COPY static static

RUN python -m pip check

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3).read()"]

CMD ["sh", "-c", "exec python -m uvicorn --factory app.runtime:create_production_app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-4} --limit-concurrency ${WEB_MAX_CONNECTIONS:-400} --backlog ${WEB_BACKLOG:-2048} --timeout-keep-alive 5 --proxy-headers --forwarded-allow-ips=\"${TRUSTED_PROXY_CIDRS:-127.0.0.1}\""]
