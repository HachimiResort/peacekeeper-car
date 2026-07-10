#!/bin/sh
set -eu

case "${WEB_CONSOLE_DEMO_MODE:-false}" in
  true|TRUE|1) demo=true ;;
  *) demo=false ;;
esac

printf 'window.__PEACEKEEPER_CONFIG__ = { demoMode: %s };\n' "$demo" \
  > /usr/share/nginx/html/runtime-config.js
