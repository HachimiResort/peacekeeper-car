# Peacekeeper Compose CI/CD

This repository now ships with a GitHub Actions pipeline focused on the
Docker Compose control-plane stack:

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `.github/workflows/deploy.yml`
- `compose.prod.yaml`

## What each workflow does

### CI

`CI` runs on pull requests and on pushes to `main`, `feature/**`, and
`codex/**`.

It validates:

- `apps/mission-api` tests and Docker buildability
- `apps/web-console` `generate:api`, `lint`, `typecheck`, `test`, and `build`
- checked-in OpenAPI artifacts stay in sync
- the local development `compose.yaml` stack can boot and pass health checks

### Release Images

`Release Images` runs on every push to `main`.

It builds and pushes two images to Aliyun Container Registry:

- `crpi-1kkkjpeehkoxqf8e.cn-shanghai.personal.cr.aliyuncs.com/<namespace>/peacekeeper-mission-api:sha-<commit>`
- `crpi-1kkkjpeehkoxqf8e.cn-shanghai.personal.cr.aliyuncs.com/<namespace>/peacekeeper-web-console:sha-<commit>`

It also updates the moving `:main` tag for both images.

### Deploy Compose

`Deploy Compose` is manual only.

The deploy workflow uploads:

- `compose.prod.yaml`
- `.deploy-images.env`

Then it runs on the target server:

```bash
docker compose --env-file .env --env-file .deploy-images.env -f compose.prod.yaml pull
docker compose --env-file .env --env-file .deploy-images.env -f compose.prod.yaml up -d --remove-orphans
```

This keeps PostgreSQL and map volumes intact and avoids `down -v`.

## Aliyun repository setup

Yes, for this repository it is best to create two private image repositories
under the same namespace:

- `peacekeeper-mission-api`
- `peacekeeper-web-console`

That matches your two custom Compose services and keeps rollback and image
permissions straightforward.

## Server layout

Each deployment target should have a directory like:

```text
/opt/peacekeeper/
  .env
  compose.prod.yaml
  .deploy-images.env
```

And a car seed file outside the repository checkout, for example:

```text
/opt/peacekeeper/config/cars.yaml
```

`compose.prod.yaml` mounts that file through `PEACEKEEPER_CARS_PATH`, so
server-side car configuration is no longer coupled to a Git working tree.

## Required server prerequisites

- Docker Engine
- Docker Compose v2
- `curl`
- a directory referenced by `DEPLOY_PATH`
- a pre-created `.env` file in that directory

## Required server `.env`

Create `.env` on the target server with values like:

```dotenv
POSTGRES_DB=peacekeeper
POSTGRES_USER=peacekeeper
POSTGRES_PASSWORD=replace-me
PEACEKEEPER_SHARED_TOKEN=replace-me-too
PEACEKEEPER_CARS_PATH=/opt/peacekeeper/config/cars.yaml
STATUS_POLL_S=1.0
STATUS_TIMEOUT_S=2.0
WEB_CONSOLE_DEMO_MODE=false
```

`MISSION_API_IMAGE` and `WEB_CONSOLE_IMAGE` do not belong in `.env`; the deploy
workflow writes them into `.deploy-images.env` for each rollout.

## GitHub Environments

Create one GitHub Environment:

- `production`

Store deploy credentials there. The deploy workflow is manual, so production
approval rules can stay as strict or as loose as you want.

## Required GitHub Secrets

At the repository level, keep using your existing Aliyun registry secrets:

- `ALIYUN_DOCKER_HUB_NAMESPACE`
- `ALIYUN_DOCKER_HUB_USER`
- `ALIYUN_DOCKER_HUB_TOKEN`

For the `production` environment:

- `DEPLOY_HOST`
- `DEPLOY_USER`
- `DEPLOY_SSH_KEY`
- `DEPLOY_KNOWN_HOSTS`

If your Aliyun repositories are private, the deploy workflow will reuse the
same repository-level `ALIYUN_DOCKER_HUB_USER` and
`ALIYUN_DOCKER_HUB_TOKEN` to log the server into the registry before `pull`.

As an Environment variable, not a secret:

- `DEPLOY_PATH`
