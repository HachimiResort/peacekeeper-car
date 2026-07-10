#!/usr/bin/env bash
set -euo pipefail

REPO_ON_HOST="${REPO_ON_HOST:-$PWD/../..}"
REPO_IN_CONTAINER="${REPO_IN_CONTAINER:-/root/peacekeeper-car}"
IMAGE="${IMAGE:-icar/ros-foxy:1.0.2}"
NAME="${NAME:-pk-agent}"
CONFIG="${CONFIG:-../../configs/fleet-agent.direct.yaml}"

docker run --rm -it \
  --name "$NAME" \
  --net=host \
  --privileged \
  -e PEACEKEEPER_SHARED_TOKEN="${PEACEKEEPER_SHARED_TOKEN:-}" \
  -v /dev:/dev \
  -v "$REPO_ON_HOST:$REPO_IN_CONTAINER" \
  "$IMAGE" \
  bash -lc "if test -f /opt/ros/foxy/setup.bash; then source /opt/ros/foxy/setup.bash; fi && if test -f /root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash; then source /root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash; fi && if test -f $REPO_IN_CONTAINER/robot/ros2_ws/install/setup.bash; then source $REPO_IN_CONTAINER/robot/ros2_ws/install/setup.bash; fi && cd $REPO_IN_CONTAINER/robot/fleet-agent && python3 -m pip install -r requirements.txt && python3 agent.py --config $CONFIG"
