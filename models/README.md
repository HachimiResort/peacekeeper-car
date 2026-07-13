# Jetson TensorRT models

This directory holds deployment-only model artifacts. It is mounted into the
vehicle container at `/root/peacekeeper-car/models` by the existing fleet-agent
launcher.

`yolov8n.engine` must be built on the target Jetson with the installed TensorRT
version; do not commit or copy an engine generated on another machine. The
generic detection API is not tied to cats: `configs/fleet-agent.*.yaml` sets
`target_labels` to `cat` initially and can list additional labels later.

The model remains disabled by default. The ROS container does not include the
NVIDIA Python inference stack, so run `fleet_agent.vision_worker` on the
Jetson host. It binds to `127.0.0.1:8092`; because fleet-agent uses host
networking, it forwards JPEG frames to that loopback worker without opening a
second camera device.

On the Jetson host, start it with:

```bash
cd /home/jetson/peacekeeper-car
./robot/fleet-agent/run_vision_worker.sh
```

The worker loads and validates the TensorRT engine before it begins listening
on port 8092. Once `/health` responds, capture requests do not pay a model
cold-start cost.

Set `VISION_ENGINE` when the engine is stored elsewhere. The command only
starts inference HTTP on loopback; it does not open `/dev/video0`.
