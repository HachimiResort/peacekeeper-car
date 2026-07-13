#!/usr/bin/env bash
# Run on the Jetson host, not inside the ROS fleet-agent container.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ENGINE="${VISION_ENGINE:-$REPO_ROOT/models/yolov8n.engine}"
export PYTHONPATH="$REPO_ROOT/robot/fleet-agent${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -m fleet_agent.vision_worker --engine "$ENGINE" "$@"
