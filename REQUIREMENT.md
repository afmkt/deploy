## Git SSH Deploy Tool

CLI toolkit for deployment to either a remote host over SSH or the local machine. It combines Git sync, Docker image transfer, reverse proxy bootstrap/management, and service scaffolding/deploy commands.

## Installation
- ./dist/deploy is the only binary executable output
- ./scripts/build.sh is the command to build the binary output

## Open Source Status

This repository is prepared for open-source use as a binary CLI tool.

- Project model: uv-managed source + standalone binary release artifact
- Primary release artifact: `dist/deploy`
- CI verifies tests on push and pull requests
- Tagged releases can publish platform binaries and checksums

## Requirements

- Python 3.12+
- `uv` for local development/build workflows
- Docker available locally (for `image push`)
- Docker available on the target machine
- SSH access to remote host when targeting a remote machine



## Non-interactive mode
```sh
deploy ... --non-interactive ...
```
Disable all user interaction when resolving arguments. The resolution process only involves configuration file and CLI arguments.

## Show CLI version:
```sh
deploy --version
```


## Top-Level Commands

```text
repo
image
proxy
svc
```

Global options:

- `--help`
- `--version`

## Core Workflows

### 1) Git Push to Remote

```sh
deploy repo push --remote <host> --port 22 --username <user> --key <ssh_key> --path ~/.deploy/repos
```
After the command we will have
- `~/.deploy/repos` is the default remote path, can be overriden by CLI argument or config.yml
- A bare Git repository at `~/.deploy/repos/<repo-name>.git`
- A work directory of the repo at `~/.deploy/repos/<repo-name>.work`
  The branch of the remote work dir matches the local work dir
  The revision of the remote work dir matches the local work dir
- A configuration file in the local repo work directory `.deploy/config.yml`

Use saved config on later runs:

```sh
deploy repo push
```

Run the same workflow on the local machine:

```sh
deploy repo push --remote localhost --path ~/.deploy/repos
```

### 2) Git Pull from Remote

```sh
deploy repo pull --remote <host> --port 22 --username <user> --key <ssh_key> --path ~/.deploy/repos
```
- This command will commit any changes in the remote work dir and push to the bare repo
- Pull from the remote bare repo to the local repo
- Doesn't checkout the remote branch

Local target example:
```sh
deploy repo pull --remote localhost --path ~/.deploy/repos
```

### 3) Push Docker Image to Target

```sh
deploy image push --image <image_name:tag> --remote <host> --username <user> --key <ssh_key>
```
- This command will use `docker save -o ...` to package the image to a tar ball
- Send the tar ball to remote via ssh
- Load the tar ball at the remote machine by `docker load -i ...`

Notes:

- Detects target architecture and pulls/saves an appropriate image variant.
- Transfers tarball via SFTP for remote targets

```sh
deploy image build --tag <image_name:tag> --remote <host> --username <user> --key <ssh_key>
```
- `--tag` assign a tag to the result image
- This command will sync the local repository to the remote machine first
- Then it will build image in the remote work dir

## Proxy Management (Bridge Mode)

The proxy stack uses `lucaslorentz/caddy-docker-proxy`.

By default, proxy and services use one external Docker network: `ingress`.
For shared hosts running multiple applications, you can attach the proxy to multiple
networks and keep each application isolated on its own network.

Commands:

```sh
deploy proxy up --remote <host> --username <user> --key <ssh_key> --network <name> --network <name1> ... --bootstrap
```
After the command we will have
- local configuration file config.yml
- `~/.deploy/repos/docker-caddy-proxy.service/docker-compose.yml`, this is the compose file to launch the proxy
- `~/.deploy/repos/docker-caddy-proxy.service/Caddyfile`, this is the configuration for the proxy

```sh
deploy proxy status 
```
display the status of the proxy

```sh
deploy proxy logs 
```
display the recent logs of the proxy

```sh
deploy proxy down 
```
stop the proxy

Useful options:
- `--remote localhost`: Run the same proxy workflow on the current machine instead of over SSH.


### Native Caddy Bootstrap Behavior

When `proxy up` detects native Caddy on the remote machine and bootstrap handoff is enabled with `--bootstrap`:

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
deploy svc init --domain api.example.com --name api --image <image_name:tag> --port 8000 --network <name> --network <name1> ... --global --path-prefix /api/auth
```
- `deploy svc init` execute locally without connecting to remote machine, the `--port` is not SSH port but the port number of the service.

Useful options:

- `--image <image_name:tag>`: Required for `svc init`. If omitted on CLI, resolve from configuration first; if still missing and interactive mode is enabled, prompt the user; in `--non-interactive` mode, fail with an error.
- `--network <name>`: External network that this service joins for caddy routing (default: `ingress`).
- `--global`: Mark the service as globally exposed so it joins every ingress network configured on the proxy.
- `--path-prefix <path>`: Route only traffic under this path prefix on the shared domain (e.g. `/api/auth`). Allows multiple services to share one domain via path-based routing.
- When `--domain` is not specified, the service as internal-only — no Caddy labels, no ingress network. The container is reachable only by other containers on the same Docker network.

Example with isolated app network:

This command generates:
- `.deploy/config.yml` - configuration file
- `Dockerfile`
- `docker-compose.yml`
- `.github/skills/deploy-service/SKILL.md` — generated service-specific operating guidance

### Deliver Image And Start Service

`svc up` reads routing intent from local `docker-compose.yml` (scaffolded by
`svc init`) and starts the service. Image delivery is performed separately.


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
deploy svc init --domain api.example.com --network app-a --image api:latest
deploy image build --tag api:latest --remote <host> --username <user> --key <ssh_key>
deploy svc up --remote <host> --username <user> --key <ssh_key>
```

Example with a globally exposed service:

```sh
deploy svc init --domain api.example.com --global --image api:latest
deploy image build --tag api:latest --remote <host> --username <user> --key <ssh_key>
deploy svc up --remote <host> --username <user> --key <ssh_key>
```

### Path-Based Routing — Multiple Services on One Domain

When several services share a domain, use `--path-prefix` to assign each one a
path scope. The root-owning service (no prefix) catches all unmatched traffic;
prefixed services only handle requests under their path.

```sh
# Auth UI — owns the domain root
deploy svc init --domain auth.example.com --name auth-ui --image auth-ui:latest
deploy svc up --name auth-ui --remote <host> --username <user> --key <ssh_key>

# Auth API — owns /api/auth/* only; prefix is stripped before forwarding
deploy svc init --domain auth.example.com --name auth-api --path-prefix /api/auth --image auth-api:latest
deploy svc up --name auth-api --remote <host> --username <user> --key <ssh_key>
```

Both containers must join the same ingress network. Caddy merges them into one
virtual host. `handle_path` strips the prefix before the request reaches the
upstream, so the service sees `/login` for an incoming `/api/auth/login` request.

### Internal Services — No Public Routing

For caches, databases, background workers, and other containers that must not be
exposed to the internet, don't use `--domain` during `svc init`. No Caddy labels or ingress network
membership are added.

```sh
deploy svc init --name session-store --image session-store:latest
deploy svc up --name session-store --remote <host> --username <user> --key <ssh_key>
```

The container is reachable by name from other containers on the same Docker Compose project network.


```sh
# Set up services with their network and domain
deploy svc init --network app-a --domain a.example.com --image <image:a>
deploy svc init --network app-b --domain b.example.com --image <image:b>

# Start shared proxy, resolve missing argument from configuration file
deploy proxy up --network app-a --network app-b

# Deploy services (configuration comes from docker-compose.yml)
deploy svc up --network app-a --remote <host> --username <user> --key <ssh_key>
deploy svc up --network app-b --remote <host> --username <user> --key <ssh_key>
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
deploy svc init --name <service> --domain <correct-host> --image <image_name:tag>
deploy svc up --name <service> --remote <host> --username <user> --key <ssh_key>
```


Notes:

- CLI args override saved config values.
- Passwords are not persisted.

## Orthogonality Contract

When designing or reviewing command flows, enforce this contract:

1. Resolve: Convert CLI/config/session inputs into a resolved argument object with all defaults and fallback decisions finalized. The resolution process may include user interaction unless `--non-interactive` is specified.
2. Execute: Run workflow logic strictly from resolved arguments; avoid reading raw CLI/config again in execution steps.
3. Persist on success: Write resolved configuration or session state only after successful execution.
4. Missing CLI arguments are resolved in this order: CLI value, then configuration value, then interactive prompt (interactive mode only). If still unresolved, report error and quit.
5. Each command is a separate flow, but the resolution of arguments are shared among all commands.
6. When the program start, it passes 
   a. all the validated CLI argument 
   b. the configuration file in the current dir 
   c. the argument profile, e.g., a list of required arguments of the current command to the ArgumentResolver. 
7. ArgumentResolver will return fully resolved arguments to the command. If an required argument is not specified in CLI and not found in configuration file, the ArgumentResolver may ask the user for that argument in interactive mode. In non-interactive mode, unsolved arguments result in an error.



## Configuration format
- YAML file
- Organized by top-level command, for example
```yaml
repo:
    push:
        remote: localhost
        port: 22
        username: user
        key: ...
        path: ~/.deploy/repos
    pull:
        remote: 47.100.30.18
        ...
image:
    build:
        remote: ...
        ...
    push:
        ...
svc:
    init:
        ...
    up:
        ...
    down:
        ...
    status:
        ...
proxy:
    up:
        ...
    down:
        ...
    status:
        ...
    logs:
        ...
```
Each command reads and updates its own section. 

Validation rules:

- Unknown keys are an error.
- Required keys are defined by each Click command option contract (for example, required options declared in Click for `svc init`, `repo push`, etc.).
- Path-like values support shell-style home expansion when applicable (for example `~/.deploy/repos`, SSH key file paths, and other file-system paths). This keeps config and CLI usage portable across machines and user accounts.

## Error handling
- Output clear and actionable message to the console and quit

## .github/skills/deploy-service/SKILL.md
This file is generated by `deploy svc init` and contains service-specific operating guidance. It can be customized with manual edits or by modifying the generation template. This file will help the service developers deploy and manage their services.

