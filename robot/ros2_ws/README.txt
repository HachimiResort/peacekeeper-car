# ROS2 工作空间

`robot/ros2_ws` 是每辆车的本地 ROS2 能力层，负责本地雷达和 SLAM 进程，以及围绕 `/cmd_vel` 的辅助工具。`robot/fleet-agent` 是唯一对外提供 HTTP/Web 服务的入口，也是唯一允许写入 `/dev/myserial` 的进程。

运行前提如下：

- 雷达驱动发布 `/scan`。
- `fleet-agent` 内的直连模式里程计桥接采用 Rosmaster 底盘运动反馈，发布 `/odom` 与 `odom -> base_link` TF。
- SLAM 节点消费 `/scan` 和直连模式的 `/odom`/`tf` 数据流，并发布 `/map`。
- ROS 原生工具发布的 `/cmd_vel` 统一经过 `fleet-agent` 的直连模式订阅器与仲裁器执行。
- fleet-agent 负责启动、停止这些进程并保存地图。

## 软件包

```text
src/peacekeeper_bringup/
  launch/lidar.launch.py
  launch/slam.launch.py
  peacekeeper_bringup/teleop_cmd_vel.py
  peacekeeper_bringup/topic_watchdog.py
```

启动文件和工具说明：

- `lidar.launch.py` 引入 `sllidar_ros2/sllidar_launch.py`。
- `slam.launch.py` 转发到 `yahboomcar_nav/map_gmapping_a1_launch.py`。
- `teleop_cmd_vel` 发布交互式键盘 `Twist` 指令，用于通过终端测试 ROS 控制链路。
- `topic_watchdog.py` 报告 `/cmd_vel`、`/scan` 与 `/map` 是否活跃。

## 构建

在车辆上执行：

```bash
cd /root/peacekeeper-car/robot/ros2_ws
colcon build --symlink-install
source install/setup.bash
```

然后使用 `configs/fleet-agent.direct.yaml` 启动 `fleet-agent`，确保 `/cmd_vel` 始终受车辆端仲裁器控制。

## ROS 控制链路测试

终端 A：

```bash
cd /root/peacekeeper-car/robot/fleet-agent
python3 agent.py --config ../../configs/fleet-agent.direct.yaml
```

终端 B：

```bash
source /opt/ros/foxy/setup.bash
source /root/peacekeeper-car/robot/ros2_ws/install/setup.bash
ros2 run peacekeeper_bringup teleop_cmd_vel --ros-args -p rate_hz:=5.0
```

此测试平稳时，请检查 HTTP/Web 控制层。此测试出现卡顿时，请检查直连 `/cmd_vel` 订阅器、仲裁器和 Rosmaster 指令链路。
