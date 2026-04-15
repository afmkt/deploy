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
- Core operational commands support local and remote targets via `--target`.
- Docker image transfer (`docker-push`) supports remote architecture targeting.
- `proxy` command group manages `lucaslorentz/caddy-docker-proxy`.
- Native Caddy bootstrap and port handoff is supported.
- Proxy operation is bridge-mode only.
- `service` command group scaffolds and deploys Docker-based services. Domain routing is explicit and fail-safe.

## Requirements

- Python 3.12+
- `uv` for local development/build workflows
- Docker available locally (for `docker-push`)
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

## Top-Level Commands

```text
push
pull
docker-push
proxy
service
monitor
show-config
clear-config
```

Global options:

- `--help`
- `--version`

## Core Workflows

### 1) Git Push to Remote

```sh
deploy push --host <host> --username <user> --key <ssh_key> --deploy-path /tmp/deploy/repos
```

Use saved config on later runs:

```sh
deploy push --use-config
```

Run the same workflow on the local machine:

```sh
deploy push --target local --deploy-path /tmp/deploy/repos
```

### 2) Git Pull from Remote

```sh
deploy pull --host <host> --username <user> --key <ssh_key> --deploy-path /tmp/deploy/repos
```

Local target example:

```sh
deploy pull --target local --deploy-path /tmp/deploy/repos
```

Useful options:

- `--branch <name>`: Pull into a specific local branch.
- `--commit`: If the remote working directory has uncommitted changes, commit them on the remote before pulling.
- `--sync-remote`: Sync remote working directory changes back through the remote bare repository before pulling locally. Use this when the server may contain edits that do not yet exist in your local repository.

Examples:

```sh
# Pull into a specific branch
deploy pull --branch feature-x

# Commit remote working tree changes before pulling
deploy pull --commit

# Fully sync remote working tree -> bare repo -> local repo
deploy pull --sync-remote
```

### 3) Push Docker Image to Target

```sh
deploy docker-push -i <image:tag> --host <host> --username <user> --key <ssh_key>
```

Local target example:

```sh
deploy docker-push -i <image:tag> --target local
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
deploy proxy diagnose --use-config
deploy proxy down --use-config
```

Useful options:

- `--ingress-network <name>`: Attach proxy to one or more external networks. Repeat the option or use comma-separated values.
- `--target local`: Run the proxy workflow on the current machine instead of over SSH.

Examples:

```sh
# Default single-network behavior (ingress)
deploy proxy up --use-config

# Attach proxy to multiple app networks
deploy proxy up --use-config \
    --ingress-network app-a \
    --ingress-network app-b

# Equivalent comma-separated form
deploy proxy up --use-config --ingress-network app-a,app-b
```

### Native Caddy Bootstrap Behavior

When `proxy up` detects native Caddy and bootstrap handoff is enabled:

1. Reads native Caddyfile.
2. Leaves the original native Caddy config file unchanged.
3. Rewrites loopback upstreams (`localhost`, `127.0.0.1`, `127.0.1.1`, `[::1]`) to a bridge-reachable host address in the generated bootstrap content.
4. Writes bootstrap file `/tmp/deploy/caddy-proxy/Caddyfile`.
5. Stops native Caddy service so docker-caddy-proxy can bind ports `80` and `443`.
6. Starts docker-caddy-proxy.

### Bridge Mode Prerequisite

If old native services are proxied through rewritten host addresses, those services must listen on a non-loopback interface (for example `0.0.0.0:<port>`). Loopback-only listeners cannot be reached from a bridge network container.

## Service Commands

### Scaffold Service Files

Run inside your service directory:

```sh
deploy service init -d api.example.com
```

Useful options:

- `--ingress-network <name>`: External network that this service joins for caddy routing (default: `ingress`).
- `--global-ingress`: Mark the service as globally exposed so it joins every ingress network configured on the proxy.

Example with isolated app network:

```sh
deploy service init -d api.example.com --ingress-network app-a
```

This generates:

- `Dockerfile`
- `docker-compose.yml`
- `.deploy-service.json` — local service metadata (domain, port, image, networks)

### Deploy Service to Target

`--domain` is required on every deploy. The command will not silently reuse a
stale domain from a previous deployment.

```sh
deploy service deploy -i <image:tag> -d api.example.com --host <host> --username <user> --key <ssh_key>
```

Local target:

```sh
deploy service deploy -i <image:tag> -d localhost --target local
```

Useful options:

- `--domain / -d <host>`: Public hostname for caddy routing. **Always provide this explicitly.**
- `--rebuild`: Force a fresh Docker image build from the remote build context even if the image already exists on the target.
- `--allow-remote-domain-fallback`: Opt in to reusing the domain persisted on the target when `--domain` is omitted. Not recommended; prefer always providing `--domain`.
- `--ingress-network <name>`: Use the same network name configured in `proxy up`.
- `--global-ingress`: Attach the service to every ingress network currently configured on the proxy. When `proxy up` later changes its ingress networks, globally exposed services are re-applied automatically.
- `--target local`: Deploy to the current machine instead of a remote host.

#### Domain Resolution Order

When `--domain` is omitted, the command resolves the domain in this order:

1. Local `.deploy-service.json` in the current directory (written by `service init`)
2. Persisted service metadata on the target host

If the domain can only be resolved from the target's persisted metadata (source 2),
the command warns and **exits with an error** in non-interactive mode unless
`--allow-remote-domain-fallback` is also passed. This prevents silently reusing
stale routing from a previous deployment.

#### Rebuild an Updated Service

After changing source code or dependencies, rebuild and redeploy:

```sh
deploy service deploy -d api.example.com --rebuild
```

This builds a fresh image from the remote build context and restarts the container.
Without `--rebuild`, the deploy reuses the existing image if it is already present
on the target.

#### Accessing the Service

`service status` shows the active routing information after each deploy:

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
deploy service deploy -i <image:tag> -d api.example.com \
    --ingress-network app-a \
    --host <host> --username <user> --key <ssh_key>
```

Example with a globally exposed service:

```sh
deploy service deploy -i <image:tag> -d api.example.com \
    --global-ingress \
    --host <host> --username <user> --key <ssh_key>
```

Recommended shared-host pattern:

1. Start proxy once with all application networks.
2. Deploy each application service with its own `--ingress-network` and explicit `--domain`.

```sh
deploy proxy up --use-config --ingress-network app-a --ingress-network app-b
deploy service deploy -n app-a -i <image:a> -d a.example.com --ingress-network app-a --host <host> --username <user> --key <ssh_key>
deploy service deploy -n app-b -i <image:b> -d b.example.com --ingress-network app-b --host <host> --username <user> --key <ssh_key>
```

### Check Service Status

```sh
deploy service status
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
redeploying with an explicit `--domain`:

```sh
deploy service deploy --name <service> --domain <correct-host>
```

## Monitor TUI

Run the long-running operations monitor:

```sh
deploy monitor --use-config
```

Useful options:

- `--refresh-interval <seconds>`: polling interval (default `5`)
- `--log-lines <count>`: lines fetched for log actions (default `120`)
- `--command-timeout <seconds>`: SSH timeout per remote command (default `10`)
- `--action-timeout <seconds>`: overall timeout per monitor action (default `15`)

Keybindings:

- `r`: refresh now
- `u`: proxy up
- `d`: proxy down
- `s`: start selected service
- `x`: stop selected service
- `t`: restart selected service
- `n`: create Docker network
- `l`: fetch logs for selected service (or proxy if no service is selected)
- `c`: request cancellation of in-progress action
- `q`: quit

The monitor is intentionally lightweight: it is not an orchestrator and does not
attempt Kubernetes-style reconciliation.

## Configuration

Saved config file:

```text
~/.deploy/config.json
```

Show saved config:

```sh
deploy show-config
```

Clear all config:

```sh
deploy clear-config
```

Clear one section:

```sh
deploy clear-config --command push
deploy clear-config --command pull
deploy clear-config --command proxy
deploy clear-config --command service
```

Notes:

- CLI args override saved config values.
- Passwords are not persisted.

## Command Reference

### `deploy push`

Sync a local Git repository to the deployment target.

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--repo-path` | `-r` | `.` | Path to local Git repository |
| `--host` | `-h` | | Remote server hostname or IP |
| `--port` | `-p` | `22` | SSH port |
| `--username` | `-u` | | SSH username |
| `--key` | `-k` | | Path to SSH private key |
| `--password` | | | SSH password (not recommended) |
| `--deploy-path` | `-d` | `/tmp/deploy/repos` | Deploy path on target |
| `--target` | | `auto` | `auto` \| `remote` \| `local` |
| `--use-config` | | off | Load arguments from saved config |
| `--dry-run` | | off | Validate connection without pushing |
| `--interactive/--no-interactive` | | on | Interactive mode |

---

### `deploy pull`

Pull a deployed repository back to local.

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--repo-path` | `-r` | `.` | Path to local Git repository |
| `--host` | `-h` | | Remote server hostname or IP |
| `--port` | `-p` | `22` | SSH port |
| `--username` | `-u` | | SSH username |
| `--key` | `-k` | | Path to SSH private key |
| `--password` | | | SSH password |
| `--deploy-path` | `-d` | `/tmp/deploy/repos` | Deploy path on target |
| `--branch` | `-b` | | Branch to pull into |
| `--commit` | | off | Commit remote working tree changes before pulling |
| `--sync-remote` | | off | Full sync: commit remote → push to bare → pull locally |
| `--target` | | `auto` | `auto` \| `remote` \| `local` |
| `--use-config` | | off | Load arguments from saved config |
| `--dry-run` | | off | Validate connection without pulling |

---

### `deploy docker-push`

Transfer a Docker image to the deployment target.

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--image` | `-i` | *(required)* | Docker image to transfer (`name:tag`) |
| `--host` | `-h` | | Remote server hostname or IP |
| `--port` | `-p` | `22` | SSH port |
| `--username` | `-u` | | SSH username |
| `--key` | `-k` | | Path to SSH private key |
| `--password` | | | SSH password |
| `--platform` | | *(auto-detect)* | Override target platform (e.g. `linux/amd64`) |
| `--registry-username` | | | Docker registry username for private images |
| `--registry-password` | | | Docker registry password for private images |
| `--target` | | `auto` | `auto` \| `remote` \| `local` |
| `--use-config` | | off | Load arguments from saved config |
| `--dry-run` | | off | Detect architecture without transferring |

---

### `deploy proxy`

Manage the `caddy-docker-proxy` ingress container.

All subcommands accept the shared connection options: `--host`, `--port`, `--username`, `--key`, `--password`, `--target`, `--use-config`.

#### Subcommands

| Subcommand | Description |
|------------|-------------|
| `up` | Start or ensure the proxy is running |
| `down` | Stop the proxy stack |
| `status` | Show container status |
| `logs` | Show recent proxy container logs |
| `diagnose` | Collect proxy and native Caddy diagnostics |

#### `proxy up` additional options

| Option | Default | Description |
|--------|---------|-------------|
| `--ingress-network` | `ingress` | External network to attach proxy to (repeatable or comma-separated) |
| `--migrate-native-caddy/--no-migrate-native-caddy` | on | Migrate native Caddy config and hand over ports 80/443 |
| `--interactive/--no-interactive` | on | Interactive mode |

#### `proxy logs` / `proxy diagnose` additional options

| Option | Default | Description |
|--------|---------|-------------|
| `--lines` | `80` | Number of log/journal lines to fetch |

---

### `deploy service`

Scaffold and deploy Docker-based services.

#### `deploy service init`

Scaffold `Dockerfile`, `docker-compose.yml`, and `.deploy-service.json` in the current directory.

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--domain` | `-d` | *(required)* | Public hostname for caddy routing |
| `--name` | `-n` | *(current dir)* | Service name |
| `--port` | | *(auto-detect)* | App port inside container |
| `--image` | `-i` | | Use a pre-built image instead of a build directive |
| `--ingress-network` | | `ingress` | External network for routing (repeatable or comma-separated) |
| `--global-ingress` | | off | Join every configured ingress network |
| `--force` | | off | Overwrite existing `Dockerfile` / `docker-compose.yml` |

#### `deploy service deploy`

Build or push a service image and start it on the target.

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--name` | `-n` | *(current dir)* | Service name |
| `--domain` | `-d` | | Public hostname — always provide explicitly |
| `--image` | `-i` | *(from metadata)* | Docker image name/tag |
| `--port` | | `8000` | App port inside container |
| `--rebuild` | | off | Force image rebuild even if already present on target |
| `--allow-remote-domain-fallback` | | off | Allow reusing domain from persisted target metadata |
| `--missing-image-action` | | `ask` | `ask` \| `push` \| `build` \| `abort` |
| `--auto-sync-context/--no-auto-sync-context` | | on | Auto-sync repo to target before remote build |
| `--deploy-path` | | *(from config)* | Remote base path for build context |
| `--ingress-network` | | `ingress` | External network for routing (repeatable or comma-separated) |
| `--global-ingress` | | off | Join every configured ingress network |
| `--host` | `-h` | | Remote server hostname or IP |
| `--ssh-port` | | `22` | SSH port |
| `--username` | `-u` | | SSH username |
| `--key` | `-k` | | Path to SSH private key |
| `--password` | | | SSH password |
| `--target` | | `auto` | `auto` \| `remote` \| `local` |
| `--use-config` | | on | Load SSH args from saved config |
| `--interactive/--no-interactive` | | on | Interactive mode |

#### `deploy service status`

Show routing, access URLs, and recent container logs.

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--name` | `-n` | *(current dir)* | Service name |
| `--host` | `-h` | | Remote server hostname or IP |
| `--port` | `-p` | `22` | SSH port |
| `--username` | `-u` | | SSH username |
| `--key` | `-k` | | Path to SSH private key |
| `--password` | | | SSH password |
| `--target` | | `auto` | `auto` \| `remote` \| `local` |
| `--use-config` | | on | Load SSH args from saved config |

---

### `deploy monitor`

Run the TUI monitor for proxy, services, networks, and resources.

| Option | Default | Description |
|--------|---------|-------------|
| `--host`, `--port`, `--username`, `--key`, `--password`, `--target`, `--use-config` | | Connection options (see above) |
| `--refresh-interval` | `5` | Polling interval in seconds |
| `--log-lines` | `120` | Lines to fetch per logs action |
| `--command-timeout` | `10` | Per-command SSH timeout in seconds |
| `--action-timeout` | `15` | Overall action timeout in seconds |

---

### `deploy show-config`

Print all saved configuration to stdout.

---

### `deploy clear-config`

Remove saved configuration.

| Option | Short | Description |
|--------|-------|-------------|
| `--command` | `-c` | Clear config for one section only (`push` \| `pull`) |

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
