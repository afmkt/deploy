## Git SSH Deploy Tool

CLI toolkit for remote deployment over SSH. It combines Git sync, Docker image transfer, reverse proxy migration/management, and service scaffolding/deploy commands.

## Open Source Status

This repository is prepared for open-source use as a binary CLI tool.

- Project model: uv-managed source + standalone binary release artifact
- Primary release artifact: `dist/deploy`
- CI verifies tests on push and pull requests
- Tagged releases can publish platform binaries and checksums

## Current Status

- Git push/pull workflows are stable.
- Docker image transfer (`docker-push`) supports remote architecture targeting.
- `proxy` command group manages `lucaslorentz/caddy-docker-proxy`.
- Native Caddy migration is supported.
- Proxy operation is bridge-mode only.
- `service` command group can scaffold and deploy FastAPI-style Docker services.

## Requirements

- Python 3.12+
- `uv` for local development/build workflows
- Docker available locally (for `docker-push`)
- Docker available on remote host
- SSH access to remote host
- Local virtual environment (recommended):

```sh
source .venv/bin/activate
```

## Install and Run

Preferred local execution with uv:

```sh
uv run python main.py --help
```

Run directly:

```sh
python main.py --help
```

Build standalone binary:

```sh
./scripts/build.sh
```

Binary output:

```text
dist/deploy
```

Show CLI version:

```sh
python main.py --version
./dist/deploy --version
```

## Top-Level Commands

```text
push
pull
docker-push
proxy
service
caddy
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
python main.py push --host <host> --username <user> --key <ssh_key> --deploy-path /var/repos
```

Use saved config on later runs:

```sh
python main.py push --use-config
```

### 2) Git Pull from Remote

```sh
python main.py pull --host <host> --username <user> --key <ssh_key> --deploy-path /var/repos
```

Useful options:

- `--branch <name>`: Pull into a specific local branch.
- `--commit`: If the remote working directory has uncommitted changes, commit them on the remote before pulling.
- `--sync-remote`: Sync remote working directory changes back through the remote bare repository before pulling locally. Use this when the server may contain edits that do not yet exist in your local repository.

Examples:

```sh
# Pull into a specific branch
python main.py pull --branch feature-x

# Commit remote working tree changes before pulling
python main.py pull --commit

# Fully sync remote working tree -> bare repo -> local repo
python main.py pull --sync-remote
```

### 3) Push Docker Image to Remote

```sh
python main.py docker-push -i <image:tag> --host <host> --username <user> --key <ssh_key>
```

Notes:

- Detects remote architecture and pulls/saves an appropriate image variant.
- Transfers tarball via SFTP and loads image on remote host.

## Proxy Management (Bridge Mode)

The proxy stack uses `lucaslorentz/caddy-docker-proxy`.

By default, proxy and services use one external Docker network: `ingress`.
For shared hosts running multiple applications, you can attach the proxy to multiple
networks and keep each application isolated on its own network.

Commands:

```sh
python main.py proxy up --use-config
python main.py proxy status --use-config
python main.py proxy logs --use-config --lines 120
python main.py proxy diagnose --use-config
python main.py proxy down --use-config
```

Useful option:

- `--ingress-network <name>`: Attach proxy to one or more external networks. Repeat the option or use comma-separated values.

Examples:

```sh
# Default single-network behavior (ingress)
python main.py proxy up --use-config

# Attach proxy to multiple app networks
python main.py proxy up --use-config \
	--ingress-network app-a \
	--ingress-network app-b

# Equivalent comma-separated form
python main.py proxy up --use-config --ingress-network app-a,app-b
```

### Native Caddy Migration Behavior

When `proxy up` detects native Caddy and migration is enabled:

1. Reads native Caddyfile.
2. Backs it up to `/opt/caddy-proxy/Caddyfile.native.backup`.
3. Rewrites loopback upstreams (`localhost`, `127.0.0.1`, `127.0.1.1`, `[::1]`) to a bridge-reachable host address.
4. Writes bootstrap file `/opt/caddy-proxy/Caddyfile`.
5. Stops native Caddy service.
6. Starts docker-caddy-proxy.

### Bridge Mode Prerequisite

If old native services are proxied through rewritten host addresses, those services must listen on a non-loopback interface (for example `0.0.0.0:<port>`). Loopback-only listeners cannot be reached from a bridge network container.

## Service Commands

### Scaffold Service Files

Run inside your service directory:

```sh
python main.py service init -d api.example.com
```

Useful option:

- `--ingress-network <name>`: External network that this service joins for caddy routing (default: `ingress`).

Example with isolated app network:

```sh
python main.py service init -d api.example.com --ingress-network app-a
```

This generates:

- `Dockerfile`
- `docker-compose.yml`

### Deploy Service to Remote

```sh
python main.py service deploy -i <image:tag> -d api.example.com --host <host> --username <user> --key <ssh_key>
```

Useful option:

- `--ingress-network <name>`: Use the same network name configured in `proxy up`.

Example with isolated app network:

```sh
python main.py service deploy -i <image:tag> -d api.example.com \
	--ingress-network app-a \
	--host <host> --username <user> --key <ssh_key>
```

Recommended shared-host pattern:

1. Start proxy once with all application networks.
2. Deploy each application service with its own `--ingress-network` value.

```sh
python main.py proxy up --use-config --ingress-network app-a --ingress-network app-b
python main.py service deploy -n app-a -i <image:a> -d a.example.com --ingress-network app-a --host <host> --username <user> --key <ssh_key>
python main.py service deploy -n app-b -i <image:b> -d b.example.com --ingress-network app-b --host <host> --username <user> --key <ssh_key>
```

Check status:

```sh
python main.py service status --host <host> --username <user> --key <ssh_key>
```

## Monitor TUI

Run the long-running operations monitor:

```sh
python main.py monitor --use-config
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
python main.py show-config
```

Clear all config:

```sh
python main.py clear-config
```

Clear one section:

```sh
python main.py clear-config --command push
python main.py clear-config --command pull
python main.py clear-config --command proxy
python main.py clear-config --command service
```

Notes:

- CLI args override saved config values.
- Passwords are not persisted.

## Legacy Command Group

`caddy` command group is still available for direct native Caddy management/import/apply flows, but current proxy-first workflows should prefer `proxy` + `service`.

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
