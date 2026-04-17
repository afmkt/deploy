---
name: deploy-{service_name}
description: Deploy and manage the {service_name} service with the local deploy CLI.
argument-hint: Ask for deploy commands, routing, redeploy, and troubleshooting for {service_name}.
user-invocable: true
disable-model-invocation: false
---

# Service Deployment Skill: {service_name}

Use this skill whenever a developer asks how to deploy, update, or operate this service.

## Service Profile

- Service name: {service_name}
- Domain/host: {domain_display}
- Container port: {port}
- Image default: {image_value}
- Exposure scope: {route_scope}
- Path prefix: {route_path}
- Ingress networks: {networks_value}
- Global ingress mode: {exposure_scope}

## Project Artifacts

- Dockerfile: generated and owned by `deploy svc init`
- Compose file: `docker-compose.yml`
- Skill file: `.github/skills/deploy-service/SKILL.md`

## Command Workflow

  1. Scaffold or refresh files:
          `deploy svc init -n {service_name}{domain_flag} --image {image_value}`
   This creates `Dockerfile`, `docker-compose.yml`, and this skill file.
   The compose file contains all routing and service configuration.

2. Sync source to target when needed:
      `deploy repo push`
          Required before `deploy image build`.

3. Ensure ingress proxy is running:
   `deploy proxy up`
   Starts the Caddy reverse proxy that routes traffic to services.

  4. Deliver service image to target (choose one):
      - `deploy image push --image {image_value}` for a pre-built local image.
      - `deploy image build --tag {image_value}` to build on target from synced source.

  5. Start service:
      `deploy svc up -n {service_name}`
      Reads configuration from local `docker-compose.yml` and starts the service.
      `deploy svc up` fails fast if the image does not exist on the target.

  6. Check runtime state:
      `deploy svc status -n {service_name}`
   Shows container status, IP, and routed domain.

## Configuration Source

**All service configuration is now sourced from `docker-compose.yml`:**
- Domain/routing: `caddy` label
- Container port: `expose` section
- Service image: `image` field
- Path prefix: `caddy.handle_path` label
- Ingress networks: `networks` section
- Exposure scope: `deploy.scope` label (`internal`, `single`, or `global`)

Update `docker-compose.yml` directly or re-run `svc init` with new flags to change routing.

## Execution Contract

Keep argument resolution and workflow execution orthogonal:

1. Resolve: finalize defaults/config/session values into resolved intent before execution.
2. Execute: run workflow steps only from resolved intent; avoid re-resolving raw inputs.
3. Persist on success: write config/session updates only after successful execution.

## Operational Guidance

- `deploy svc up` persists remote runtime metadata at `/tmp/deploy/repos/{service_name}.service/.deploy-service.json`.
- Update routing by editing `docker-compose.yml` or re-running `svc init`.
- For code or dependency changes: run `deploy image build --tag {image_value}` and then `deploy svc up -n {service_name}`.
- Use `deploy svc down -n {service_name}` to stop without deleting remote metadata.
- For local development, set `--remote localhost` to run workflows locally.
