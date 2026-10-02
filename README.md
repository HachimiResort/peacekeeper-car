> 本项目为北京交通大学软件学院 2026 小学期实训留档

# Peacekeeper Car

Peacekeeper 是巡逻智能车项目的车端软件仓库。

包含车辆执行层和中心控制平面服务：

- `robot/fleet-agent`：运行在每辆车上的 HTTP、WebSocket 和 Web 调试服务。
- `robot/ros2_ws`：由 Agent 管理的本地 ROS2 能力层。
- `apps/mission-api`：带认证的业务 API，负责 PostgreSQL 车辆注册、任务与事件存储、状态聚合和地图分发。
- `apps/web-console`：仅通过 Mission API 通信的 React 操作员控制台。

项目的完整演示闭环如下：

```text
启动 fleet-agent
-> 打开 ROS 控制调试页面
-> 启动雷达与 SLAM
-> 通过 /cmd_vel 手动驾驶
-> 构建并保存地图
-> 按需抓取相机画面或保存快照
-> 安全停车或紧急停车
```

车辆还支持以下直连模式闭环：

```text
加载已保存地图 -> 设置 AMCL 初始位姿 -> 发送 Nav2 目标点
-> 创建或加载多点巡逻路线 -> 安全暂停、继续或取消
```

可选的豆包语音闭环将大模型和云端凭据保留在 Mission API，车辆仅负责音频、唤醒词检测和最终的安全停车：

```text
“你好” -> 本地 KWS -> 火山引擎 ASR -> Ark Responses 与经校验的工具调用
-> 带短 TTL 的车辆动作 -> Seed TTS PCM -> 车辆扬声器
```

启用前请在 `.env.example` 所示位置设置 7 个 `PEACEKEEPER_ARK_*` 和 `PEACEKEEPER_VOLC_*` 环境变量，并在车辆配置中设置 `voice.mission_api_url`、`voice.robot_id` 与 `voice.enabled`。共享令牌通过 `PEACEKEEPER_SHARED_TOKEN` 注入；请勿将云服务凭据写入车辆 YAML。可将固定的普通话服务故障提示音放在 `data/audio/voice-service-error.wav`；文件缺失时，网关会改用本地警示音，并保持 `degraded` 状态。

直连模式的控制权规则必须严格遵守：`fleet-agent` 是唯一允许写入 `/dev/myserial` 的进程。它订阅 ROS2 `/cmd_vel`，与手动控制和紧急停车仲裁后，再调用 `Rosmaster_Lib`。

`mission-api` 是操作员客户端的正式入口。`fleet-agent` 首页仍用于工程调试，在直连部署中由同一共享令牌保护。

## 快速开始

在车辆的 `icar/ros-foxy:1.0.2` 环境中运行：

```bash
cd /root/peacekeeper-car/robot/fleet-agent
python3 -m pip install -r requirements.txt
export PEACEKEEPER_SHARED_TOKEN='<与 mission-api 相同的令牌>'
python3 agent.py --config ../../configs/fleet-agent.direct.yaml
```

然后访问：

```text
http://<车辆 IP>:8001
```

如需从 Docker 宿主机启动，请参阅 `robot/fleet-agent/run_in_docker.sh`。

在控制端计算机上运行：

```bash
cp .env.example .env
# 将 .env 中的 POSTGRES_PASSWORD 和 PEACEKEEPER_SHARED_TOKEN 换成强随机值，
# 再编辑 configs/fleet/cars.yaml。
docker compose up -d --build
curl http://127.0.0.1:28080/health/ready
```

操作员控制台地址为 `http://127.0.0.1:28081`。输入 `.env` 中的共享令牌；令牌仅保存在当前浏览器标签页。若要使用显式示例数据，请在重新创建 web-console 容器前设置 `WEB_CONSOLE_DEMO_MODE=true`。

关于车辆注册、认证和地图分发，请参阅 `apps/mission-api/README.md`；前端开发说明见 `apps/web-console/README.md`；Docker Compose 的 CI/CD 与生产部署说明见 `docs/cicd.md`。

## 主要接口

- `GET /`：ROS 控制与建图调试页面。
- `GET /api/status`：进程、ROS、视频和模式状态。
- `GET /api/video/sample.jpg`：供按需抓图界面使用的一帧相机画面。
- `POST /api/control/cmd_vel`：发布带 TTL 限制的速度指令。
- `POST /api/control/stop`：发布零速度指令。
- `POST /api/control/estop`：紧急停车。
- `POST /api/mapping/start`：启动雷达和 SLAM。
- `POST /api/mapping/save`：保存当前地图。
- `POST /api/video/snapshot`：保存当前第一视角画面。
- `GET /api/voice/status`：本地 KWS、音频设备与 Mission API 诊断信息。
- `POST /api/voice/start` / `POST /api/voice/stop`：启停车端语音网关。
