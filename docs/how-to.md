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
  --remote <host> \
  --username <user> \
  --key ~/.ssh/id_ed25519
```

On the first run this saves the connection to `.deploy/config.yml`. All
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

### Native Caddy handoff

If the host already runs native Caddy, pass `--bootstrap` to `proxy up` to
migrate its Caddyfile and hand over ports 80/443 to docker-caddy-proxy. The tool:

1. Reads the existing Caddyfile.
2. Rewrites any loopback upstreams (`localhost`, `127.0.0.1`, etc.) to the
   bridge-reachable host address so those services remain accessible from inside
   the Docker network.
3. Writes a bootstrap Caddyfile consumed by the proxy container.
4. Stops native Caddy.
5. Starts docker-caddy-proxy.

Omit `--bootstrap` (the default) if migration is not wanted.

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
deploy svc init --domain api.example.com --image <image:tag>
```

This generates:

- `Dockerfile`
- `docker-compose.yml`
- `.github/skills/deploy-service/SKILL.md` — service-specific operating guidance

The directory name becomes the service name. Override with `--name` on later
commands if needed.

### Step 2 — Push the source to the target

```sh
deploy repo push \
  --remote <host> --username <user> --key ~/.ssh/id_ed25519 \
  --path /srv/deploy/repos
```

### Step 3 — Ensure the proxy is running

```sh
deploy proxy up
```

### Step 4 — Deliver the image

Choose one mode:

- Build on target from synced source:

```sh
deploy image build --tag myapp:latest --remote <host> --username <user> --key ~/.ssh/id_ed25519
```

- Push a pre-built local image:

```sh
deploy image push --image myapp:latest --remote <host> --username <user> --key ~/.ssh/id_ed25519
```

### Step 5 — Start the service

```sh
deploy svc up --remote <host> --username <user> --key ~/.ssh/id_ed25519
```

`svc up` checks that the proxy is running, verifies the image exists on target,
uploads the compose file, and starts the container. On success it prints routing
information:

```
Container state: running
Route host: api.example.com
Metadata domain: api.example.com
Ingress access: curl http://localhost/<path>   (or curl -H "Host: api.example.com" http://localhost/<path>)
In-network access: http://myapp:8000/<path>
```

---

## 3. Redeploy after code changes

After editing source code or dependencies:

**Push updated source to target:**

```sh
deploy repo push
```

**Build a new image on target and restart:**

```sh
deploy image build --tag myapp:latest --remote <host> --username <user> --key ~/.ssh/id_ed25519
deploy svc up --remote <host> --username <user> --key ~/.ssh/id_ed25519
```

**Verify the service came back healthy:**

```sh
deploy svc status
```

---

## 4. Deploy a pre-built image (no source on target)

When the image is already available locally (e.g. built in CI) and does not need
to be rebuilt on the target.

**Transfer the image:**

```sh
deploy image push --image myapp:1.2.3 \
  --remote <host> --username <user> --key ~/.ssh/id_ed25519
```

The tool detects the target architecture and transfers the correct image variant
via SFTP.

**Deploy using the transferred image:**

```sh
deploy svc init --domain api.example.com --image myapp:1.2.3
deploy svc up --remote <host> --username <user> --key ~/.ssh/id_ed25519
```

Because the image is already present on target, `svc up` proceeds directly to
service startup.

---

## 5. Multi-service shared host with isolated networks

Each application gets its own Docker network. The proxy attaches to all of them.

**Start proxy with all application networks:**

```sh
deploy proxy up \
  --network app-a \
  --network app-b
```

Or on a single line:

```sh
deploy proxy up --network app-a --network app-b
```

**Deploy each service on its own network:**

```sh
# From the app-a repository (scaffolded with --network app-a --image app-a:latest)
deploy image push --image app-a:latest
deploy svc up

# From the app-b repository (scaffolded with --network app-b --image app-b:latest)
deploy image push --image app-b:latest
deploy svc up
```

Services on different networks cannot reach each other directly. The proxy
routes external traffic to each service via its dedicated network.

### Globally exposed services

A service that must be reachable regardless of how the proxy's network list
changes can be marked global during `svc init`:

```sh
deploy svc init --image shared:latest --domain shared.example.com --global
deploy image push --image shared:latest
deploy svc up
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
deploy svc init --domain auth.example.com --name auth-ui --image auth-ui:latest
deploy image build --tag auth-ui:latest
deploy svc up --name auth-ui
```

**Scaffold and deploy the API** (serves only `/api/auth/*`):

```sh
# Inside the auth-api repository
deploy svc init --domain auth.example.com --name auth-api --path-prefix /api/auth --image auth-api:latest
deploy image build --tag auth-api:latest
deploy svc up --name auth-api
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

Omit `--domain` from `svc init` to suppress all Caddy labels and ingress network membership.
The container joins only the default project network created by Docker Compose,
so it is reachable by name from other containers in the same compose project or
from containers explicitly added to the same network.

```sh
deploy svc init --name session-store --image session-store:latest
deploy image push --image session-store:latest
deploy svc up --name session-store
```

With no domain, the service name is used as a placeholder in metadata so
reconciliation stays consistent.

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

Set `--remote localhost` to run the same workflow on the current machine without
SSH.

**Push source locally:**

```sh
deploy repo push --remote localhost --path ~/.deploy/repos
```

**Start the proxy locally:**

```sh
deploy proxy up --remote localhost
```

**Deploy a service locally:**

```sh
deploy svc init --domain localhost --name myapp --image myapp:latest
deploy image build --tag myapp:latest --remote localhost
deploy svc up --remote localhost
```

The `localhost` domain tells Caddy to use plain HTTP (no TLS certificate
required), so you can reach the service at `http://localhost/<path>`.

**Transfer a Docker image to local:**

```sh
deploy image push --image myapp:dev --remote localhost
```

---

## 9. Bring a service down

Stop and remove the containers for a service without deleting its metadata or
compose file on the target:

```sh
deploy svc down
```

Run from the service repository root (the directory name resolves the service
name). Override with `--name` if needed:

```sh
deploy svc down --name myapp
```

To also stop the ingress proxy:

```sh
deploy proxy down
```

---

## 10. Recover remote edits back to local

When the remote working tree has been edited directly (e.g. hot-patched on the
server), pull those changes back.

**Pull from remote bare repository:**

```sh
deploy repo pull --remote <host> --username <user> --key ~/.ssh/id_ed25519
```

The remote working directory must be clean (no uncommitted changes) before
pulling. If it has uncommitted changes, commit or discard them manually on the
remote before running this command.

Pull into a specific local branch:

```sh
deploy repo pull --branch hotfix/fix-upstream
```
