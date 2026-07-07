# ROS2 Workspace

`robot/ros2_ws` is the local ROS2 capability layer for each car. In v1 it owns
the ROS chassis bridge and wraps the existing lidar and SLAM packages behind
Peacekeeper-owned launch files. `robot/fleet-agent` remains the only external
HTTP/Web entrypoint.

The v1 assumptions are:

- The chassis driver subscribes to `/cmd_vel` and is the only writer to the
  serial device.
- The v1 `rosmaster_chassis` node bridges `/cmd_vel` to `Rosmaster_Lib` and
  publishes a command-integrated `/odom` plus `odom -> base_link` TF for SLAM
  validation. This is a bring-up odometry source, not a substitute for real
  encoder odometry.
- The lidar driver publishes `/scan`.
- The SLAM node consumes `/scan`, odometry/TF from the chassis stack, and
  publishes `/map`.
- The fleet-agent starts and stops these processes, publishes manual
  `geometry_msgs/Twist` commands, and saves maps.

## Packages

```text
src/peacekeeper_bringup/
  launch/chassis.launch.py
  launch/lidar.launch.py
  launch/slam.launch.py
  launch/mapping_stack.launch.py
  peacekeeper_bringup/rosmaster_chassis.py
  peacekeeper_bringup/teleop_cmd_vel.py
  peacekeeper_bringup/topic_watchdog.py
```

The v1 launch files:

- `chassis.launch.py` starts `peacekeeper_bringup/rosmaster_chassis`.
- `lidar.launch.py` includes `sllidar_ros2/sllidar_launch.py`.
- `slam.launch.py` starts `yahboomcar_nav/slam_gmapping_X3`.
- `mapping_stack.launch.py` starts all three for the v1 mapping loop.
- `teleop_cmd_vel` publishes interactive keyboard `Twist` commands for testing
  the ROS control path without the web UI.
- `topic_watchdog.py` reports whether `/cmd_vel`, `/scan`, and `/map` are active.

## Build

On the car:

```bash
cd /root/peacekeeper-car/robot/ros2_ws
colcon build --symlink-install
source install/setup.bash
```

The default `configs/fleet-agent.example.yaml` still starts the existing
Yahboom commands directly so old images can be inspected. For current v1
bring-up, build this workspace and use `configs/fleet-agent.bringup.yaml`.

## Control Test Without Web

Terminal A:

```bash
source /opt/ros/foxy/setup.bash
source /root/peacekeeper-car/robot/ros2_ws/install/setup.bash
ros2 launch peacekeeper_bringup chassis.launch.py command_timeout_s:=2.0
```

Terminal B:

```bash
source /opt/ros/foxy/setup.bash
source /root/peacekeeper-car/robot/ros2_ws/install/setup.bash
ros2 run peacekeeper_bringup teleop_cmd_vel --ros-args -p rate_hz:=5.0
```

If this is smooth but the web UI is not, the issue is in the HTTP/Web control
layer. If this also stutters, the issue is in the ROS chassis bridge or the
Rosmaster command mode.
