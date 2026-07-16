# Peacekeeper Mission API

Mission API 是 Web 客户端、操作员工具和车队指挥服务的控制平面入口。ROS 与车辆串口由 fleet-agent 独占，Mission API 将车辆操作代理到各个已认证的 `fleet-agent`。

## 运行

在仓库根目录执行：

```bash
cp .env.example .env
# .env.example 中的密码和令牌仅供本地示例；启动前必须替换为高强度随机值。
docker compose up -d --build
docker compose ps
```

宿主机端口刻意使用高位端口：

```text
mission-api  http://127.0.0.1:28080
PostgreSQL   127.0.0.1:55433
```

健康检查接口无需认证。所有 `/api/*` 与 `/internal/*` 请求均使用共享令牌：

```bash
curl http://127.0.0.1:28080/health/ready
curl -H "X-Peacekeeper-Token: $PEACEKEEPER_SHARED_TOKEN" \
  http://127.0.0.1:28080/api/robots
```

WebSocket 客户端连接地址：

```text
ws://127.0.0.1:28080/ws/status?token=<共享令牌>
```

## 豆包语音网关

请通过进程环境变量设置全部服务商参数（根目录 `.env.example` 列出了它们）。Mission API 的基础服务可在凭据缺失时正常启动；语音连接会收到明确的 `voice_not_configured` 事件。默认 Ark 模型为 `doubao-seed-2-0-lite-260215`，已关闭深度思考和响应存储。

每辆车建立一个已认证连接：

```text
ws://<mission-api>/ws/voice/<robot-id>?token=<共享令牌>
```

车辆发送 `hello`、`turn.start`、16 kHz 单声道有符号 16 位 PCM 二进制帧和 `turn.end`。Mission API 会返回状态、最终转写、工具审计、回复文本，以及位于 `reply.audio.start` 与 `reply.audio.end` 之间的 24 kHz PCM 二进制帧。一辆车只能有一个活跃语音套接字和一个活跃对话轮次；断开连接会取消该轮次并尽力发送停车指令。

模型的工具范围包括状态查询、灯光、50–1000 ms 蜂鸣、停车、相机观察、100–1000 ms 移动，以及明确的 15–180 度原地转向。所有参数会再次通过 Pydantic 校验；移动速度固定为 0.22 m/s 或 0.9 rad/s，附带车辆 TTL，并在 `finally` 中再次发送停车指令。语音工具范围排除导航和巡逻能力。

PostgreSQL 保存会话转写、回复、已校验的工具审计、延迟和错误。存储范围排除原始麦克风音频和合成音频。Docker Compose 容器入口会自动执行 `alembic upgrade head`；非容器化部署需手动执行该命令。

## 车辆注册表

PostgreSQL 是权威数据源。`configs/fleet/cars.yaml` 是启动种子，只向数据库插入尚未登记的车辆 ID。已有记录的更新和删除均通过数据库操作完成。

```yaml
robots:
  - id: car_1
    name: Peacekeeper Car 1
    base_url: http://10.60.162.192:8001
    role: leader
    enabled: true
    capabilities:
      mapping: true
      navigation: true
      patrol: true
```

所有 mission-api 与 fleet-agent 必须配置相同的 `PEACEKEEPER_SHARED_TOKEN`。中心端每秒轮询一次已启用车辆；连续 3 次失败后，车辆会被标记为离线。

## 地图生命周期

1. 在车辆上保存地图。
2. 调用 `POST /api/maps/import-from-robot`，提交 `robot_id` 与 `map_name`。
3. Mission API 校验 ZIP、PGM、YAML 与哈希，然后将不可变元数据保存到 PostgreSQL，将文件保存到 `mission_map_data`。
4. 调用 `POST /api/maps/{map_id}/dispatch`，提交目标 `robot_ids`。
5. 每个目标车辆以原子方式安装 `logical_name__vN.yaml/.pgm`。
6. 使用安装后的名称显式启动 Nav2，再发布初始位姿。地图分发在安装完成后结束。

地图压缩包必须且只能包含 `manifest.json`、`map.yaml` 和 `map.pgm`，压缩前后均限制为 64 MiB。车辆处于 SLAM、Nav2、巡逻或地图保存状态时，禁止安装地图。

## 数据持久化与检查

`mission_pg_data` 保存 PostgreSQL 数据，`mission_map_data` 保存地图文件，`mission_evidence_data` 保存风险截图和元数据。`docker compose down -v` 会删除这三类数据；该命令只适用于明确的数据清理操作。

```bash
python3 -m compileall apps/mission-api/mission_api apps/mission-api/migrations
docker compose build mission-api
docker compose up -d
```

Alembic 迁移会创建车辆、任务、幂等事件、告警、事件证据、地图和地图部署记录。服务启动时会将遗留的 `pending`/`running` 任务调整为 `failed/service_restart`。

部署能力包括共享令牌认证、数据库和本地文件存储。请部署在受信任网络中。外部网络部署必须配置反向代理、TLS 和用户授权。
