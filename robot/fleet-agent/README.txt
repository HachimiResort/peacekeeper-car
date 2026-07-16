# Fleet Agent

`fleet-agent` 是车辆端执行服务，提供以下能力：

- 第一视角 Web 控制调试页面。
- HTTP 与 WebSocket 状态 API。
- 直接执行 Rosmaster 指令，支持手动驾驶和由 ROS 控制的 `/cmd_vel`。
- 直接发布用于 SLAM 和保存地图的 ROS 里程计/TF。
- 管理雷达与 SLAM 的 ROS2 进程。
- 按需抓取相机画面并保存快照。
- 通过 `map_saver_cli` 保存地图。
- 直接启动 Nav2、设置 AMCL 初始位姿和发送 `NavigateToPose` 目标。
- 顺序多点巡逻路线：支持暂停、继续、取消、驻留和循环控制。
- 经过认证的地图包导出，以及原子化、带版本的地图安装。

点击 `Sample` 会请求一帧 JPEG。按需抓图可降低 ROS 控制和建图期间的 Wi-Fi 负载。视频服务优先使用车辆镜像中已有的 `cv2`，缺少 OpenCV 时返回静态 JPEG。

## 控制权归属

fleet-agent 始终以直连模式运行。它独占 `/dev/myserial`，持续运行 ROS2 `/cmd_vel` 订阅器，并在调用 `Rosmaster_Lib` 前，将每一条底盘指令交给本地仲裁器处理。

```text
ros2_ws /cmd_vel -> fleet-agent 订阅器 -> 指令仲裁器 -> Rosmaster_Lib -> /dev/myserial -> 车辆
Web 手动指令     -> fleet-agent HTTP  -> 指令仲裁器 -> Rosmaster_Lib -> /dev/myserial -> 车辆
Rosmaster 运动反馈 -> 直连里程计桥接 -> /odom + /tf -> SLAM / map_saver_cli
```

请使用 `configs/fleet-agent.direct.yaml` 或等效的直连模式配置。不要启动其他会写入 `/dev/myserial` 的进程。

激光跟踪可作为同一条直连链路中的小型实验：

```text
laser_Tracker_a1_X3 -> /cmd_vel -> fleet-agent 订阅器 -> Rosmaster_Lib
```

使用直连配置时，请从已加载 ROS2 和 Yahboom 工作空间的环境启动 agent，确保进程内 `/cmd_vel` 订阅器可以导入 `rclpy` 与 `geometry_msgs`。订阅器随 agent 一同启动。仲裁器进入 `LASER_TRACKING` 或 `NAV_PATROL` 等 ROS 接管模式后执行 ROS `/cmd_vel`。

建图时，`Start Mapping` 会启动雷达和 SLAM。应用会持续运行直连模式里程计/TF 桥接；桥接调用 `Rosmaster_Lib.get_motion_data()` 获取真实底盘运动反馈。控制板提供运动反馈时，已安装的 `yahboomcar_nav` gmapping 启动文件可获得 `/odom` 和 `odom -> base_link` TF。`Save Map` 会对当前 `/map` 话题执行 `map_saver_cli`，并写入 `<data_dir>/maps/<名称>.yaml` 和 `<data_dir>/maps/<名称>.pgm`。默认保存命令设置 `save_map_timeout:=120000`；若 `map_saver_cli` 报超时，agent 会重试一次，因为较大的 Yahboom 地图在高负载下保存较慢。

导航时，应先停止 SLAM，再使用 `Start Nav2` 加载已保存地图；在地图上点击机器人的实际位置以发布初始位姿，然后发送较近的目标点。fleet-agent 仲裁器进入 `NAV_PATROL` 模式后接收 Nav2 的 `/cmd_vel` 数据流。

巡逻可运行 `configs/patrol_routes.yaml` 中已保存的路线，或运行在 Web 地图上点击构建的草稿路线。路线文件使用 `yaw_deg`；HTTP 巡逻点的 `yaw` 使用弧度。暂停会取消当前 Nav2 目标，继续会重新发送同一个路线点。手动控制、停车、紧急停车和关闭 Nav2 都会先在本地取消巡逻，再变更控制权。

直连激光跟踪启动后车辆无动作时，请检查 `GET /api/status`：在 `ros.direct_cmd_vel_subscriber` 中，`ready`、`spin_thread_alive` 和 `enabled` 应为 true，跟踪运行时 `message_count` 应递增，`ros_import_error` 应为 null；在 `ros.direct_odom_bridge` 中，`ready` 应为 true，车辆移动时 `pose` 应变化；在 `ros.arbiter` 中，被拒绝的指令会显示在 `last_reject_reason`。

## 运行

在车辆容器内执行：

```bash
cd /root/peacekeeper-car/robot/fleet-agent
python3 -m pip install -r requirements.txt
export PEACEKEEPER_SHARED_TOKEN='<共享令牌>'
python3 agent.py --config ../../configs/fleet-agent.direct.yaml
```

或在宿主机上执行：

```bash
cd robot/fleet-agent
CONFIG=../../configs/fleet-agent.direct.yaml ./run_in_docker.sh
```

访问：

```text
http://<车辆 IP>:8001
```

直连配置和示例配置都要求令牌。调试页面仅将令牌保存到当前标签页的 `sessionStorage`；API 客户端通过 `X-Peacekeeper-Token` 发送令牌，WebSocket 客户端使用 `?token=`。本地测试配置可关闭该要求。

## 主要 API

```text
GET  /
GET  /api/status
GET  /api/video/sample.jpg
GET  /video.mjpg
WS   /ws

GET  /api/vision/status
POST /api/vision/capture
GET  /api/vision/latest
GET  /api/vision/latest.jpg
GET  /api/vision/stream.mjpg

POST /api/control/cmd_vel
POST /api/control/manual_cmd
POST /api/control/lights
POST /api/control/buzzer
POST /api/control/ros_cmd_vel/start
POST /api/control/ros_cmd_vel/stop
POST /api/control/stop
POST /api/control/estop
POST /api/system/clear_estop

GET  /api/audio/assets
GET  /api/audio/status
POST /api/audio/install
POST /api/audio/play
POST /api/audio/stop
GET  /api/voice/status
POST /api/voice/start
POST /api/voice/stop
POST /api/voice/trigger-wake

POST /api/process/start
POST /api/process/stop
POST /api/process/stop_all

POST /api/mapping/start
POST /api/mapping/save
POST /api/mapping/stop

GET  /api/mapping/meta
GET  /api/mapping/latest
GET  /api/mapping/preview.png
GET  /api/mapping/live-meta
GET  /api/mapping/live.png
GET  /api/maps/export?name=<地图名称>
GET  /api/maps/saved
POST /api/maps/install
GET  /api/navigation/status
POST /api/navigation/start
POST /api/navigation/initial_pose
POST /api/navigation/goal
POST /api/navigation/cancel
POST /api/navigation/stop

GET  /api/patrol/routes
POST /api/patrol/routes/reload
GET  /api/patrol/status
POST /api/patrol/start
POST /api/patrol/pause
POST /api/patrol/resume
POST /api/patrol/cancel

GET  /api/hazards/status
POST /api/hazards/monitor
POST /api/hazards/{event_key}/hold
POST /api/hazards/{event_key}/takeover
POST /api/hazards/{event_key}/resume

POST /api/tracking/laser/start
POST /api/tracking/laser/stop

POST /api/show/prepare
POST /api/show/commit
POST /api/show/abort
GET  /api/show/status

POST /api/video/start_record
POST /api/video/stop_record
POST /api/video/snapshot
```

手动控制指令示例：

```json
{
  "linear_x": 0.2,
  "linear_y": 0.0,
  "angular_z": 0.0,
  "ttl_ms": 500,
  "source": "web"
}
```

内联巡逻请求示例：

```json
{
  "map_name": "lab_first_map",
  "loop": false,
  "points": [
    {"name": "point_1", "x": 1.2, "y": 0.8, "yaw": 0.0, "dwell_s": 2.0},
    {"name": "point_2", "x": 2.6, "y": 1.1, "yaw": 1.57, "dwell_s": 1.0}
  ]
}
```

## 验收闭环

1. 启动 agent。
2. 打开 Web 页面，仅在需要相机画面时点击 `Sample`。
3. 点击 `Start Mapping`。
4. 使用 `W/A/S/D/Q/E` 手动驾驶。
5. 点击 `Save Map`。
6. 保存快照。
7. 按下 `Emergency Stop`，确认车辆停止。

## 风险巡逻闭环

部署前请配置 `hazards.robot_id`、`hazards.mission_api_url` 和共享令牌。车辆必须能访问 Mission API 地址（例如 `http://<服务器>:28080`）。请发布经过标定的 `base_link -> camera_link` 变换，以便换算目标的地图坐标。确认停车独立于相机 TF；相机 TF 缺失时，目标地图坐标记为未知。

调用 `POST /api/hazards/monitor {"enabled": true}` 开启后台监控。监控进程独立于浏览器运行。在 1.5 m 范围内，5 次观测中有 3 次确认测量时，巡逻会暂停并进入 `HAZARD_HOLD`。证据会写入 `data/evidence`，进入 `data/outbox` 队列，并持续重试，直至中心端接收。风险保持激活时，不要使用通用的巡逻继续接口。

如需在停车指令后播放自定义告警音，可从 Web Console 上传支持的 `.mp3`、`.wav`、`.ogg` 或 `.m4a` 文件，或将文件复制到 `/root/peacekeeper-car/data/audio`。配置中只填写文件名：

```yaml
hazards:
  alarm_audio_asset: cat-alert.mp3
  alarm_audio_volume: 100
  alarm_audio_loop: true
```

车辆处于 `HOLDING` 时告警音会循环播放，在恢复巡逻或操作员接管前停止。停车、证据采集和事件投递独立于音频播放。播放失败会记录在 `GET /api/hazards/status` 的 `alarm_audio` 中。车辆测试前，请在 `pk-agent` 内确认 `command -v ffplay` 和 `/dev/snd`，然后使用已有的 Web Console 音频控件确认选定扬声器能正常出声。

## 无硬件验证

```bash
python3 -m unittest discover robot/fleet-agent/tests
python3 -m compileall robot/fleet-agent robot/ros2_ws/src/peacekeeper_bringup
```
