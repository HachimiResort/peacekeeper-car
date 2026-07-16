# Jetson TensorRT 模型

此目录保存仅用于部署的模型产物。现有的 fleet-agent 启动脚本会将其挂载到车辆容器的 `/root/peacekeeper-car/models`。

`yolov8n.engine` 必须在目标 Jetson 上，使用其已安装的 TensorRT 版本构建；不要提交或复制在其他机器上生成的 engine。通用检测 API 按 `target_labels` 识别目标；`configs/fleet-agent.*.yaml` 初始配置为 `cat`，可按需增加其他标签。

基础默认配置和示例配置中，视觉模型处于禁用状态。当前 `configs/fleet-agent.direct.yaml` 已启用视觉能力，部署前必须准备好 engine 和工作进程。请在 Jetson 宿主机上运行 `fleet_agent.vision_worker`，为 ROS 容器提供 NVIDIA Python 推理能力。工作进程监听 `127.0.0.1:8092`，通过宿主机网络接收 fleet-agent 转发的 JPEG 帧，并保持相机设备由 fleet-agent 独占。

在 Jetson 宿主机执行：

```bash
cd /home/jetson/peacekeeper-car
./robot/fleet-agent/run_vision_worker.sh
```

工作进程会在监听 8092 端口前加载并校验 TensorRT engine。`/health` 可响应后，抓图请求无需再承担模型冷启动开销。

若 engine 存放在其他位置，请设置 `VISION_ENGINE`。该命令在回环地址启动推理 HTTP 服务，全程复用 fleet-agent 提交的 JPEG 帧。
