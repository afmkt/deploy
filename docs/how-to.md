# How-To Scenarios

Practical step-by-step guides for common deployment workflows.

For option-by-option argument resolution and command behavior, see `docs/cli-reference.md`.

---

## Table of Contents

1. [Set up the ingress proxy](#1-set-up-the-ingress-proxy)
2. [First deploy of a new service](#2-first-deploy-of-a-new-service)
3. [Redeploy after code changes](#3-redeploy-after-code-changes)
4. [Deploy a pre-built image (no source on target)](#4-deploy-a-pre-built-image-no-source-on-target)
5. [Multi-service shared host with isolated networks](#5-multi-service-shared-host-with-isolated-networks)
6. [Path-based routing — multiple services on one domain](#6-path-based-routing--multiple-services-on-one-domain)
7. [Internal services — no public routing](#7-internal-services--no-public-routing)
8. [Local machine target (dev / testing)](#8-local-machine-target-dev--testing)
9. [Bring a service down](#9-bring-a-service-down)
10. [Recover remote edits back to local](#10-recover-remote-edits-back-to-local)
11. [Monitor and operate services interactively](#11-monitor-and-operate-services-interactively)

---

## 1. Set up the ingress proxy

The proxy (`lucaslorentz/caddy-docker-proxy`) must be running before any service
can receive routed traffic. This is a one-time setup per host.

### Prerequisites

- Docker installed on the target host.
- SSH access (key-based recommended).

### Steps

**Save connection details so you don't repeat them on every command:**

```sh
deploy proxy up \
  --host <host> \
  --username <user> \
  --key ~/.ssh/id_ed25519
```

On the first run this saves the connection to `~/.deploy/config.json`. All
subsequent commands can use `--use-config` (which is the default for `proxy`
subcommands) to skip repeating the flags.

**Verify the proxy is running:**

```sh
deploy proxy status
```

**View proxy logs:**

```sh
deploy proxy logs --lines 50
```

**Troubleshoot a failed start:**

```sh
deploy proxy diagnose
```

This collects the proxy container logs, the generated Caddyfile, the bootstrap
Caddyfile, and native Caddy journal output — all in one output.

### Native Caddy handoff

If the host already runs native Caddy, `proxy up` detects it and prompts whether
to migrate its Caddyfile and hand over ports 80/443 to docker-caddy-proxy. Accept
to proceed; the tool:

1. Reads the existing Caddyfile.
2. Rewrites any loopback upstreams (`localhost`, `127.0.0.1`, etc.) to the
   bridge-reachable host address so those services remain accessible from inside
   the Docker network.
3. Writes a bootstrap Caddyfile consumed by the proxy container.
4. Stops native Caddy.
5. Starts docker-caddy-proxy.

If the migration is not wanted, pass `--no-migrate-native-caddy`.

**Important:** services that native Caddy was proxying via loopback must listen
on `0.0.0.0:<port>` (not `127.0.0.1:<port>`) to be reachable from inside a
bridge network container.

### Stopping the proxy

```sh
deploy proxy down
```

---

## 2. First deploy of a new service

Run these steps once when introducing a service to a host for the first time.

### Step 1 — Scaffold service files

Inside the service repository root:

```sh
deploy service init -d api.example.com
```

This generates:

- `Dockerfile`
- `docker-compose.yml`

The directory name becomes the service name. Override with `--name` on later
commands if needed.

### Step 2 — Push the source to the target

```sh
deploy push \
  --host <host> --username <user> --key ~/.ssh/id_ed25519 \
  --deploy-path /srv/deploy/repos
```

### Step 3 — Ensure the proxy is running

```sh
deploy proxy up
```

### Step 4 — Deploy the service

```sh
deploy service deploy --host <host> --username <user> --key ~/.ssh/id_ed25519
```

`service deploy` checks that the proxy is running, resolves/builds the image from
local `docker-compose.yml` intent, uploads the compose file, and starts the
container. On success it prints the routing information:

```
Route host: api.example.com
Ingress access: curl -H "Host: api.example.com" http://localhost/<path>
In-network access: http://myapp:8000/<path>
```

---

## 3. Redeploy after code changes

After editing source code or dependencies:

**Push updated source to target:**

```sh
deploy push
```

**Rebuild image and restart:**

```sh
deploy service deploy --host <host> --username <user> --key ~/.ssh/id_ed25519 --rebuild
```

`--rebuild` forces a fresh `docker build` from the remote source tree even if an
image with the same tag already exists on the target. Without it, the existing
image is reused.

**Verify the service came back healthy:**

```sh
deploy service status
```

---

## 4. Deploy a pre-built image (no source on target)

When the image is already available locally (e.g. built in CI) and does not need
to be rebuilt on the target.

**Transfer the image:**

```sh
deploy docker-push -i myapp:1.2.3 \
  --host <host> --username <user> --key ~/.ssh/id_ed25519
```

The tool detects the target architecture and transfers the correct image variant
via SFTP.

**Deploy using the transferred image:**

```sh
deploy service init -d api.example.com -i myapp:1.2.3
deploy service deploy --host <host> --username <user> --key ~/.ssh/id_ed25519
```

Because the image is already present, `service deploy` skips the build/push
step entirely.

---

## 5. Multi-service shared host with isolated networks

Each application gets its own Docker network. The proxy attaches to all of them.

**Start proxy with all application networks:**

```sh
deploy proxy up \
  --ingress-network app-a \
  --ingress-network app-b
```

Or equivalently with comma-separated values:

```sh
deploy proxy up --ingress-network app-a,app-b
```

**Deploy each service on its own network:**

```sh
# From the app-a repository (scaffolded with --ingress-network app-a -i app-a:latest)
deploy service deploy

# From the app-b repository (scaffolded with --ingress-network app-b -i app-b:latest)
deploy service deploy
```

Services on different networks cannot reach each other directly. The proxy
routes external traffic to each service via its dedicated network.

### Globally exposed services

A service that must be reachable regardless of how the proxy's network list
changes can be marked global during `service init`:

```sh
deploy service init -i shared:latest -d shared.example.com --global-ingress
deploy service deploy
```

When `proxy up` is later run with a different set of networks, globally exposed
services are automatically re-attached to the new network set.

---

## 6. Path-based routing — multiple services on one domain

Use this when several services share one domain name and are distinguished only
by URL path.  For example, `auth.example.com` serves the auth UI at `/` and the
auth API at `/api/auth`.

Caddy's `handle_path` directive matches traffic under a path prefix and strips
the prefix before forwarding, so the upstream service sees a clean path.

### Example: auth UI + auth API on the same domain

**Scaffold and deploy the UI** (owns the domain root — no `--path-prefix`):

```sh
# Inside the auth-ui repository
deploy service init -d auth.example.com --name auth-ui
deploy service deploy --name auth-ui
```

**Scaffold and deploy the API** (serves only `/api/auth/*`):

```sh
# Inside the auth-api repository
deploy service init -d auth.example.com --name auth-api --path-prefix /api/auth
deploy service deploy --name auth-api
```

The proxy merges both containers into a single virtual host.  Requests to
`/api/auth/...` reach `auth-api`; everything else falls through to `auth-ui`.

### How `--path-prefix` affects the generated compose file

Without `--path-prefix` a service owns the whole domain:

```yaml
labels:
  caddy: auth.example.com
  caddy.reverse_proxy: "{{upstreams 8000}}"
```

With `--path-prefix /api/auth`:

```yaml
labels:
  caddy: auth.example.com
  caddy.handle_path: /api/auth*
  caddy.handle_path.reverse_proxy: "{{upstreams 8000}}"
```

The `*` wildcard is appended automatically. Trailing slashes or wildcards in the
supplied value are normalised before the label is written.

### Notes

- Both services must join the same ingress network so Caddy can discover them.
- The path prefix is stripped before the request reaches the upstream.  If your
  API is mounted at `/`, it will receive `/login` for an incoming
  `/api/auth/login` request.
- There is no limit on how many services can share one domain; each one must use
  a unique, non-overlapping prefix.

---

## 7. Internal services — no public routing

Some services (caches, databases, background workers, sidecars) must be
reachable by other containers but must not be exposed to the internet.

Pass `--internal` to `service init` to suppress all Caddy labels and ingress network membership.
The container joins only the default project network created by Docker Compose,
so it is reachable by name from other containers in the same compose project or
from containers explicitly added to the same network.

```sh
deploy service init --name session-store --internal
deploy service deploy --name session-store
```

`--domain` is not required for internal services.  If omitted, the service name
is used as a placeholder in metadata so reconciliation stays consistent.

The generated compose file contains no `caddy.*` labels and no `networks:`
section:

```yaml
services:
  session-store:
    image: redis:alpine
    container_name: session-store
    expose:
      - "6379"
    labels:
      deploy.scope: internal
    restart: unless-stopped
```

---

## 8. Local machine target (dev / testing)

Set `--host localhost` to run the same workflow on the current machine without
SSH.

**Push source locally:**

```sh
deploy push --host localhost --deploy-path /tmp/deploy/repos
```

**Start the proxy locally:**

```sh
deploy proxy up --host localhost
```

**Deploy a service locally:**

```sh
deploy service init -d localhost -n myapp
deploy service deploy --host localhost
```

The `localhost` domain tells Caddy to use plain HTTP (no TLS certificate
required), so you can reach the service at `http://localhost/<path>`.

**Transfer a Docker image to local:**

```sh
deploy docker-push -i myapp:dev --host localhost
```

---

## 9. Bring a service down

Stop and remove the containers for a service without deleting its metadata or
compose file on the target:

```sh
deploy service down
```

Run from the service repository root (the directory name resolves the service
name). Override with `--name` if needed:

```sh
deploy service down --name myapp
```

To also stop the ingress proxy:

```sh
deploy proxy down
```

---

## 10. Recover remote edits back to local

When the remote working tree has been edited directly (e.g. hot-patched on the
server), pull those changes back.

**Simple pull (clean remote working tree):**

```sh
deploy pull --host <host> --username <user> --key ~/.ssh/id_ed25519
```

**Commit remote changes, then pull:**

Use `--commit` when the remote working tree has uncommitted edits that should be
preserved:

```sh
deploy pull --commit
```

This commits the remote edits in place before pulling so nothing is lost.

**Full sync (remote edits not yet in the bare repo):**

Use `--sync-remote` when the remote working directory contains changes that have
not been pushed to the on-host bare repository yet:

```sh
deploy pull --sync-remote
```

This chains the steps automatically:
1. Commits any uncommitted changes on the remote working tree.
2. Pushes them from the remote working tree to the remote bare repo.
3. Pulls from the bare repo to your local repository.

Pull into a specific local branch:

```sh
deploy pull --sync-remote --branch hotfix/fix-upstream
```

---

## 11. Monitor and operate services interactively

The monitor TUI gives a live view of service state and allows common operations
without re-typing commands.

**Start the monitor:**

```sh
deploy monitor --use-config
```

**Useful options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--refresh-interval` | `5` | Polling interval in seconds |
| `--log-lines` | `120` | Lines fetched per log action |
| `--command-timeout` | `10` | SSH timeout per remote command (seconds) |
| `--action-timeout` | `15` | Overall timeout per monitor action (seconds) |

**Keybindings:**

| Key | Action |
|-----|--------|
| `r` | Refresh now |
| `u` | Proxy up |
| `d` | Proxy down |
| `s` | Start selected service |
| `x` | Stop selected service |
| `z` | Stop and remove selected service deployment |
| `t` | Restart selected service |
| `n` | Create Docker network |
| `l` | Fetch logs (selected service, or proxy if none selected) |
| `c` | Cancel in-progress action |
| `q` | Quit |
