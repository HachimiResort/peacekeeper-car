#!/bin/sh
set -eu

alembic upgrade head
exec uvicorn mission_api.app:app --host 0.0.0.0 --port 8080
