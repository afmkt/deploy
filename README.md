## Git SSH Deploy Tool

CLI toolkit for remote deployment over SSH. It combines Git sync, Docker image transfer, reverse proxy migration/management, and service scaffolding/deploy commands.

## Current Status

- Git push/pull workflows are stable.
- Docker image transfer (`docker-push`) supports remote architecture targeting.
- `proxy` command group manages `lucaslorentz/caddy-docker-proxy`.
- Native Caddy migration is supported.
- Proxy operation is bridge-mode only.
- `service` command group can scaffold and deploy FastAPI-style Docker services.

## Requirements

- Python 3.12+
- Docker available locally (for `docker-push`)
- Docker available on remote host
- SSH access to remote host
- Local virtual environment (recommended):

```sh
source .venv/bin/activate
```

## Install and Run

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

## Top-Level Commands

```text
push
pull
docker-push
proxy
service
caddy
show-config
clear-config
```

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

Commands:

```sh
python main.py proxy up --use-config
python main.py proxy status --use-config
python main.py proxy logs --use-config --lines 120
python main.py proxy diagnose --use-config
python main.py proxy down --use-config
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

This generates:

- `Dockerfile`
- `docker-compose.yml`

### Deploy Service to Remote

```sh
python main.py service deploy -i <image:tag> -d api.example.com --host <host> --username <user> --key <ssh_key>
```

Check status:

```sh
python main.py service status --host <host> --username <user> --key <ssh_key>
```

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
