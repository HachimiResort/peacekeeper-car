# Peacekeeper Web Console

用于操作 Mission API 的 React 操作员控制台。浏览器统一通过 Mission API 访问车辆能力；fleet-agent、ROS 和车辆串口均位于服务端链路内。

## 使用 Docker 运行

在仓库根目录执行：

```bash
cp .env.example .env
docker compose up -d --build
```

访问 `http://127.0.0.1:28081`，并使用为 Mission API 配置的同一个 `PEACEKEEPER_SHARED_TOKEN`。令牌保存在 `sessionStorage`，操作员名称保存在 `localStorage`。

使用以下命令显式开启演示模式：

```bash
WEB_CONSOLE_DEMO_MODE=true docker compose up -d --force-recreate web-console
```

## 本地开发

```bash
npm install
npm run dev
```

Vite 会将 `/api`、`/health` 和 `/ws` 代理到 `http://127.0.0.1:28080`。`npm run dev:demo` 使用本地演示数据启动前端。

## 接口契约与检查

Mission API 导出 `apps/mission-api/openapi.json`。API 变更后请重新生成仓库中提交的 TypeScript 接口契约：

```bash
npm run generate:api
npm run lint
npm run typecheck
npm run test
npm run build
```
