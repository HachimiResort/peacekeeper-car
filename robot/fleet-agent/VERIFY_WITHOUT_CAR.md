# Verify Without A Car

No-car verification cannot prove motors, lidar, camera, serial wiring, or SLAM
quality. It can prove that the vehicle-side software contract is internally
consistent before hardware testing.

## What Is Proven Locally

- Config defaults load.
- `/cmd_vel` safety watchdog publishes a zero command after TTL expiry.
- Video service can serve and save a valid fallback JPEG when no camera exists.
- Map save orchestration sanitizes names, invokes the saver command, and checks
  expected `.yaml`/`.pgm` outputs.
- Python syntax for `fleet-agent` and `peacekeeper_bringup` is valid.

## Commands

From the repository root:

```bash
python3 -m unittest discover robot/fleet-agent/tests
python3 -m compileall robot/fleet-agent robot/ros2_ws/src/peacekeeper_bringup
```

If FastAPI and PyYAML are installed locally, the web demo can also run without a
car using mock `/cmd_vel` and fake long-running processes:

```bash
cd robot/fleet-agent
python3 agent.py --config ../../configs/fleet-agent.local.yaml
```

Then open:

```text
http://127.0.0.1:8001
```

In local mode, movement commands are recorded by the mock publisher rather than
sent to ROS2. `Start Mapping` starts harmless sleep processes, and `Save Map`
creates dummy map files under `/tmp/peacekeeper-car-data`.

## What Still Requires A Car

- The chassis driver actually subscribing to `/cmd_vel`.
- Serial ownership and `/dev/myserial` behavior.
- Lidar `/scan` publication.
- SLAM `/map` quality and `map_saver_cli` compatibility.
- Real camera FPS and image quality.
