# syntax=docker/dockerfile:1.7
FROM python:3.13-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

FROM base AS builder
# WeasyPrint needs Pango/Cairo at runtime; build tools are only in this stage
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
COPY requirements/base.txt requirements/base.txt
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -r requirements/base.txt

FROM base AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core postgresql-client curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home /app --shell /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" DJANGO_SETTINGS_MODULE=config.settings
COPY --chown=app:app manage.py gunicorn.conf.py ./
COPY --chown=app:app config config
COPY --chown=app:app apps apps
COPY --chown=app:app templates templates
COPY --chown=app:app static static
COPY --chown=app:app scripts scripts
# collectstatic needs settings to import, so it runs with throw-away values that never reach the image env
RUN SECRET_KEY=build-only DATABASE_URL=postgres://u:p@localhost/db python manage.py collectstatic --noinput \
    && mkdir -p /data/media && chown -R app:app /data /app/staticfiles
USER app
ENV MEDIA_ROOT=/data/media
VOLUME ["/data/media"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz/ || exit 1
CMD ["gunicorn", "config.wsgi:application", "-c", "gunicorn.conf.py"]
