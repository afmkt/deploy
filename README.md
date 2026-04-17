## Git SSH Deploy Tool

CLI toolkit for deployment to either a remote host over SSH or the local machine. It combines Git sync, Docker image transfer, reverse proxy bootstrap/management, and service scaffolding/deploy commands.

## Open Source Status

This repository is prepared for open-source use as a binary CLI tool.

- Project model: uv-managed source + standalone binary release artifact
- Primary release artifact: `dist/deploy`
- CI verifies tests on push and pull requests
- Tagged releases can publish platform binaries and checksums

## Current Status

- Git push/pull workflows are stable.
- Core operational commands derive local vs remote behavior from connection profile (`--remote localhost` for local runs).
- Docker image transfer (`image push`) supports remote architecture targeting.
- `proxy` command group manages `lucaslorentz/caddy-docker-proxy`.
- Native Caddy bootstrap and port handoff is supported.
- Proxy operation is bridge-mode only.
- `service` command group scaffolds and deploys Docker-based services. Domain routing is explicit and fail-safe.
- Path-based routing allows multiple services to share one domain via `--path-prefix`.
- Internal-only services (caches, sidecars) can be deployed without `--domain` to suppress public routing.

## Requirements

- Python 3.12+
- `uv` for local development/build workflows
- Docker available locally (for `image push`)
- Docker available on the target machine
- SSH access to remote host when targeting a remote machine

## Install and Run

Build and install the binary:

```sh
./scripts/build.sh
cp dist/deploy ~/bin/deploy
```

Show CLI version:

```sh
deploy --version
```

For development, run directly from source:

```sh
source .venv/bin/activate
python main.py --help
# or
uv run python main.py --help
```

## Documentation Map

- CLI argument and command reference (resolution-first): `docs/cli-reference.md`
- Workflow playbooks and end-to-end scenarios: `docs/how-to.md`

## Command Groups

```text
repo
image
proxy
svc
```

Global options:

- `--help`
- `--version`
- `--non-interactive`: Disable all interactive prompts (required for scripted/CI use).

## Core Workflows

### 1) Git Push to Remote

```sh
deploy repo push --remote <host> --username <user> --key <ssh_key> --path ~/.deploy/repos
```

Use saved config on later runs:

```sh
deploy repo push --use-config
```

Run the same workflow on the local machine:

```sh
deploy repo push --remote localhost --path ~/.deploy/repos
```

### 2) Git Pull from Remote

```sh
deploy repo pull --remote <host> --username <user> --key <ssh_key> --path ~/.deploy/repos
```

Local target example:

```sh
deploy repo pull --remote localhost --path ~/.deploy/repos
```

Useful options:

- `--branch <name>`: Pull into a specific local branch.

Examples:

```sh
# Pull into a specific branch
deploy repo pull --branch feature-x
```

### 3) Push Docker Image to Target

```sh
deploy image push --image <image:tag> --remote <host> --username <user> --key <ssh_key>
```

Local target example:

```sh
deploy image push --image <image:tag> --remote localhost
```

Notes:

- Detects target architecture and pulls/saves an appropriate image variant.
- Transfers tarball via SFTP for remote targets, or via a local file copy for local targets.

## Proxy Management (Bridge Mode)

The proxy stack uses `lucaslorentz/caddy-docker-proxy`.

By default, proxy and services use one external Docker network: `ingress`.
For shared hosts running multiple applications, you can attach the proxy to multiple
networks and keep each application isolated on its own network.

Commands:

```sh
deploy proxy up --use-config
deploy proxy status --use-config
deploy proxy logs --use-config --lines 120
deploy proxy down --use-config
```

Useful options:

- `--network <name>`: Attach proxy to one or more external networks. Repeat the option to add multiple networks.
- `--remote localhost`: Run the same proxy workflow on the current machine instead of over SSH.

Examples:

```sh
# Default single-network behavior (ingress)
deploy proxy up --use-config

# Attach proxy to multiple app networks
deploy proxy up --use-config \
    --network app-a \
    --network app-b
```

### Native Caddy Bootstrap Behavior

When `proxy up` detects native Caddy and bootstrap handoff is enabled:

1. Reads native Caddyfile.
2. Leaves the original native Caddy config file unchanged.
3. Rewrites loopback upstreams (`localhost`, `127.0.0.1`, `127.0.1.1`, `[::1]`) to a bridge-reachable host address in the generated bootstrap content.
4. Writes bootstrap file `~/.deploy/repos/docker-caddy-proxy.service/Caddyfile`.
5. Stops native Caddy service so docker-caddy-proxy can bind ports `80` and `443`.
6. Starts docker-caddy-proxy.

### Bridge Mode Prerequisite

If old native services are proxied through rewritten host addresses, those services must listen on a non-loopback interface (for example `0.0.0.0:<port>`). Loopback-only listeners cannot be reached from a bridge network container.

## Service Commands

### Scaffold Service Files

Run inside your service directory:

```sh
deploy svc init -d api.example.com
```

Useful options:

- `--network <name>`: External network that this service joins for caddy routing (default: `ingress`).
- `--global`: Mark the service as globally exposed so it joins every ingress network configured on the proxy.
- `--path-prefix <path>`: Route only traffic under this path prefix on the shared domain (e.g. `/api/auth`). Allows multiple services to share one domain via path-based routing.
- When `--domain` is not specified, the service is internal-only — no Caddy labels, no ingress network. The container is reachable only by other containers on the same Docker network.

Example with isolated app network:

```sh
deploy svc init -d api.example.com --network app-a
```

Example with a path prefix (API lives at `/api/auth` on a shared domain):

```sh
deploy svc init -d auth.example.com --name auth-api --path-prefix /api/auth
```

Example for an internal service (no public routing):

```sh
deploy svc init --name session-store
```

This generates:

- `.deploy/config.yml` - configuration file
- `Dockerfile`
- `docker-compose.yml`
- `.github/skills/deploy-service/SKILL.md` — generated service-specific operating guidance

### Deliver Image And Start Service

`svc up` reads routing intent from local `docker-compose.yml` (scaffolded by
`svc init`) and starts the service. Image delivery is performed separately.

Build image on target from synced source:

```sh
deploy image build --tag <image:tag> --remote <host> --username <user> --key <ssh_key>
```

Or push a pre-built local image:

```sh
deploy image push --image <image:tag> --remote <host> --username <user> --key <ssh_key>
```

Then start service:

```sh
deploy svc up --remote <host> --username <user> --key <ssh_key>
```

Local target:

```sh
deploy svc up --remote localhost
```

Useful options:

- `--remote localhost`: Deploy to the current machine instead of a remote host.

Notes:

- `docker-compose.yml` is required for `svc up`.
- For non-internal services, `docker-compose.yml` must include a `caddy:` label.
- If compose uses `build: .` and no explicit `image:`, the default image name is `<service-name>:latest`.
- `svc up` fails if the image does not exist on the target host.

#### Update An Existing Service

After changing source code or dependencies, build and redeploy:

```sh
deploy image build --tag <image:tag>
deploy svc up
```

#### Accessing the Service

`svc status` shows the active routing information after each deploy:

```
Route host: api.example.com
Metadata domain: api.example.com
Ingress access: curl http://localhost/<path>   (or curl -H "Host: api.example.com" http://localhost/<path>)
In-network access: http://<service-name>:8000/<path>
```

- **Ingress access** goes through caddy-docker-proxy on port 80/443.
  - If the route host is `localhost`, use `curl http://localhost/<path>`.
  - Otherwise, use `curl -H "Host: <route-host>" http://localhost/<path>` (or point DNS/`/etc/hosts` to the machine).
- **In-network access** is container-to-container on the ingress Docker network, bypassing the proxy.

Example with isolated app network:

```sh
deploy svc init -d api.example.com --network app-a
deploy image build --tag api:latest --remote <host> --username <user> --key <ssh_key>
deploy svc up --remote <host> --username <user> --key <ssh_key>
```

Example with a globally exposed service:

```sh
deploy svc init -d api.example.com --global
deploy image build --tag api:latest --remote <host> --username <user> --key <ssh_key>
deploy svc up --remote <host> --username <user> --key <ssh_key>
```

### Path-Based Routing — Multiple Services on One Domain

When several services share a domain, use `--path-prefix` to assign each one a
path scope. The root-owning service (no prefix) catches all unmatched traffic;
prefixed services only handle requests under their path.

```sh
# Auth UI — owns the domain root
deploy svc init -d auth.example.com --name auth-ui
deploy svc up --name auth-ui --remote <host> --username <user> --key <ssh_key>

# Auth API — owns /api/auth/* only; prefix is stripped before forwarding
deploy svc init -d auth.example.com --name auth-api --path-prefix /api/auth
deploy svc up --name auth-api --remote <host> --username <user> --key <ssh_key>
```

Both containers must join the same ingress network. Caddy merges them into one
virtual host. `handle_path` strips the prefix before the request reaches the
upstream, so the service sees `/login` for an incoming `/api/auth/login` request.

### Internal Services — No Public Routing

For caches, databases, background workers, and other containers that must not be
exposed to the internet, omit `--domain` during `svc init`. No Caddy labels or ingress network
membership are added.

```sh
deploy svc init --name session-store
deploy svc up --name session-store --remote <host> --username <user> --key <ssh_key>
```

`--domain` is optional for internal services. The container is reachable by name
from other containers on the same Docker Compose project network.

Recommended shared-host pattern:

1. Scaffold each service with its own `--network` and explicit `--domain`.
2. Start proxy once with all application networks.
3. Deploy each service.

```sh
# Set up services with their network and domain
deploy svc init -n app-a -d a.example.com --network app-a -i <image:a>
deploy svc init -n app-b -d b.example.com --network app-b -i <image:b>

# Start shared proxy
deploy proxy up --use-config --network app-a --network app-b

# Deploy services (configuration comes from docker-compose.yml)
deploy svc up -n app-a --remote <host> --username <user> --key <ssh_key>
deploy svc up -n app-b --remote <host> --username <user> --key <ssh_key>
```

### Check Service Status

```sh
deploy svc status
```

Output includes:

- Container state (`running`, `restarting`, etc.)
- Active route host (caddy label on the running container)
- Persisted metadata domain
- Ingress access command (with `Host:` header hint when needed)
- In-network access URL
- Warning if the active route host does not match the persisted domain
- Recent container logs

A mismatch between route host and metadata domain means the running container is
routing for a different hostname than the current metadata records. Fix it by
re-running `svc init` with the correct domain and then deploying:

```sh
deploy svc init --name <service> -d <correct-host>
deploy svc up --name <service> --remote <host> --username <user> --key <ssh_key>
```

## Configuration

Saved config file:

```text
.deploy/config.yml
```

Notes:

- CLI args override saved config values.
- Passwords are not persisted.

## Command Reference

Global option: `--non-interactive` disables all interactive prompts (required for CI/scripted use).

### `deploy repo push`

Sync a local Git repository to the deployment target.

| Option | Default | Description |
|--------|---------|-------------|
| `--repo-path` | `.` | Path to local Git repository |
| `--remote` | | Remote server hostname or IP |
| `--port` | `22` | SSH port |
| `--username` | | SSH username |
| `--key` | | Path to SSH private key |
| `--password` | | SSH password (not recommended) |
| `--path` | `~/.deploy/repos` | Deploy path on target |
| `--use-config/--no-use-config` | on | Load arguments from saved config |
| `--dry-run` | off | Validate connection without pushing |

---

### `deploy repo pull`

Pull a deployed repository back to local.

| Option | Default | Description |
|--------|---------|-------------|
| `--repo-path` | `.` | Path to local Git repository |
| `--remote` | | Remote server hostname or IP |
| `--port` | `22` | SSH port |
| `--username` | | SSH username |
| `--key` | | Path to SSH private key |
| `--password` | | SSH password |
| `--path` | `~/.deploy/repos` | Deploy path on target |
| `--branch` | | Branch to pull into |
| `--use-config/--no-use-config` | on | Load arguments from saved config |
| `--dry-run` | off | Validate connection without pulling |

---

### `deploy proxy`

Manage the `caddy-docker-proxy` ingress container.

All subcommands accept the shared connection options: `--remote`, `--port`, `--username`, `--key`, `--password`, `--use-config`.

#### Subcommands

| Subcommand | Description |
|------------|-------------|
| `up` | Start or ensure the proxy is running |
| `down` | Stop the proxy stack |
| `status` | Show container status |
| `logs` | Show recent proxy container logs |

#### `proxy up` additional options

| Option | Default | Description |
|--------|---------|-------------|
| `--network` | | External network to attach proxy to (repeatable) |
| `--bootstrap/--no-bootstrap` | off | Migrate native Caddy config and hand over ports 80/443 |

#### `proxy logs` additional options

| Option | Default | Description |
|--------|---------|-------------|
| `--lines` | `80` | Number of log lines to fetch |

---

### `deploy svc`

Scaffold and operate Docker-based services.

#### `deploy svc init`

Scaffold `Dockerfile`, `docker-compose.yml`, and a service skill file in the current directory.

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--domain` | `-d` | *(optional)* | Public hostname for caddy routing (omit for internal-only) |
| `--name` | `-n` | *(current dir)* | Service name |
| `--port` | | *(auto-detect)* | App port inside container |
| `--image` | `-i` | | Use a pre-built image instead of a build directive |
| `--network` | | `ingress` | External network for routing (repeatable) |
| `--global` | | off | Join every configured ingress network |
| `--path-prefix` | | | Route only traffic under this path prefix |
| `--force` | | off | Overwrite existing `Dockerfile` / `docker-compose.yml` |

#### `deploy svc up`

Start a service on the target using an existing image.

| Option | Default | Description |
|--------|---------|-------------|
| `--name` | *(current dir)* | Service name |
| `--remote` | | Remote server hostname or IP |
| `--port` | `22` | SSH port |
| `--username` | | SSH username |
| `--key` | | Path to SSH private key |
| `--password` | | SSH password |
| `--use-config/--no-use-config` | on | Load SSH args from saved config |

#### `deploy svc status`

Show routing, access URLs, and recent container logs.

| Option | Default | Description |
|--------|---------|-------------|
| `--name` | *(current dir)* | Service name |
| `--remote` | | Remote server hostname or IP |
| `--port` | `22` | SSH port |
| `--username` | | SSH username |
| `--key` | | Path to SSH private key |
| `--password` | | SSH password |
| `--use-config/--no-use-config` | on | Load SSH args from saved config |

#### `deploy svc down`

Stop and remove service containers for one service.

| Option | Default | Description |
|--------|---------|-------------|
| `--name` | *(current dir)* | Service name |
| `--remote` | | Remote server hostname or IP |
| `--port` | `22` | SSH port |
| `--username` | | SSH username |
| `--key` | | Path to SSH private key |
| `--password` | | SSH password |
| `--use-config/--no-use-config` | on | Load SSH args from saved config |

---

### `deploy image`

Deliver Docker images to a target host.

#### `deploy image push`

Transfer a pre-built local image to the target host.

| Option | Default | Description |
|--------|---------|-------------|
| `--image` | *(required)* | Docker image to transfer (`name:tag`) |
| `--platform` | | Target platform override |
| `--registry-username` | | Docker registry username for private images |
| `--registry-password` | | Docker registry password for private images |
| `--remote` | | Remote server hostname or IP |
| `--port` | `22` | SSH port |
| `--username` | | SSH username |
| `--key` | | Path to SSH private key |
| `--password` | | SSH password |
| `--use-config/--no-use-config` | on | Load SSH args from saved config |
| `--dry-run` | off | Detect architecture without transferring |

#### `deploy image build`

Build an image on the target from the synced repository.

| Option | Default | Description |
|--------|---------|-------------|
| `--tag` | *(required)* | Docker image tag to build |
| `--path` | *(from saved repo push config)* | Remote base path used for synced repository |
| `--remote` | | Remote server hostname or IP |
| `--port` | `22` | SSH port |
| `--username` | | SSH username |
| `--key` | | Path to SSH private key |
| `--password` | | SSH password |
| `--use-config/--no-use-config` | on | Load SSH args from saved config |

---

## Development

Run tests:

```sh
uv run pytest
```

Build binary:

```sh
./scripts/build.sh
```

## Binary Verification

For release binaries, verify checksums before execution:

```sh
shasum -a 256 dist/deploy
```

Compare the output with the published release checksum.
