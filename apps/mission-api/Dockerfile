FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system peacekeeper && adduser --system --ingroup peacekeeper peacekeeper

COPY pyproject.toml /app/
COPY mission_api /app/mission_api
COPY alembic.ini /app/
COPY migrations /app/migrations
COPY docker-entrypoint.sh /app/

RUN sed -i 's/\r$//' /app/docker-entrypoint.sh \
    && pip install --no-cache-dir . \
    && chmod +x /app/docker-entrypoint.sh \
    && mkdir -p /var/lib/peacekeeper/maps /app/config \
    && chown -R peacekeeper:peacekeeper /var/lib/peacekeeper /app

USER peacekeeper

EXPOSE 8080

ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint.sh"]
