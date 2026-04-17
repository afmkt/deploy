# CLI Reference

This reference is organized to match the code paths in main.py and the flow resolvers.

## 1) Argument Resolution Model

Connection arguments are resolved through shared helpers in deploy/session.py.

Resolution order for connection profile fields (host, port, username, key):

1. CLI input
2. Saved config for the command section (when --use-config is enabled)
3. Fallback command sections in this order: push, pull, docker-push, proxy, service, monitor
4. Interactive prompts (only when interactive mode is enabled)
5. Validation failure (non-interactive remote mode requires host and username)

Notes:

- Fallback only applies when the current command config is incomplete for a remote profile.
- Local targets are skipped as remote fallback sources.
- For port, default 22 is treated as unresolved and may be replaced by saved config.
- Password is not loaded from saved config; it comes from CLI input (or interactive SSH prompt paths).

Path argument resolution:

- push/pull repo_path and deploy_path use load_defaulted_value:
  - if CLI value equals the command default, saved config value is used when present
  - otherwise CLI value is used
- image build-remote deploy_path uses this order:
  1. --deploy-path
  2. saved push deploy_path
  3. interactive prompt default
  4. failure in non-interactive mode

Config defaults by command:

- push: --use-config defaults to false
- pull: --use-config defaults to false
- docker-push: --use-config defaults to false
- image push/build-remote: --use-config defaults to true
- proxy subcommands: --use-config defaults to true
- svc up/status/down: --use-config defaults to true
- monitor: --use-config defaults to true

## 2) Argument Catalog

## Connection arguments

- --host, -h: Target host or IP
- --port, -p: SSH port (used by most commands)
- --ssh-port: SSH port used by svc up and image subcommands
- --username, -u: SSH username
- --key, -k: SSH private key path
- --password: SSH password
- --use-config/--no-use-config: Enable or disable config loading
- --interactive/--no-interactive: Enable or disable prompts

Resolution details:

- Present in push, pull, docker-push, image subcommands, proxy subcommands, svc up/status/down, monitor.
- Resolved by PushArgumentResolver, PullArgumentResolver, DockerPushArgumentResolver, ProxyUpArgumentResolver, ServiceDeployArgumentResolver, and _build_connection_from_config.

## Repository and deploy-path arguments

- --repo-path, -r: Local repository path (push, pull)
- --deploy-path, -d: Remote deploy base path (push, pull)
- --deploy-path: Remote deploy base path used by push/pull or image build-remote
- --branch, -b: Pull target branch
- --commit/--no-commit: Commit remote working directory changes before pull
- --sync-remote/--no-sync-remote: Sync remote working tree through bare repo before pull

## Image and registry arguments

- --image, -i: Docker image name:tag (docker-push, image push, image build-remote, svc init)
- --platform: Target platform override for docker-push
- --registry-username: Registry auth username for private pull
- --registry-password: Registry auth password for private pull

## Routing and ingress arguments

- --domain, -d: Public hostname for service routing (svc init)
- --name, -n: Service name override
- --port: Container app port (svc init)
- --ingress-network (repeatable/comma-separated): Ingress networks
- --global-ingress/--no-global-ingress: Attach service to all ingress networks
- --path-prefix: Path-based route prefix
- --internal: Internal-only service mode
- --allow-remote-domain-fallback: Allow reading domain from target metadata when --domain is omitted

## Proxy and monitor operational arguments

- --migrate-native-caddy/--no-migrate-native-caddy: Native Caddy handoff behavior in proxy up
- --lines: Log/diagnostic line count in proxy logs/diagnose
- --refresh-interval: Monitor polling interval seconds
- --log-lines: Monitor logs action line count
- --command-timeout: SSH per-command timeout for monitor
- --action-timeout: Overall monitor action timeout

## Config management arguments

- clear-config: optional --command, -c with choices push or pull

## 3) Command Catalog

## Root commands

### deploy push

Operation:

- Validates local repository
- Ensures target repository layout
- Pushes local changes to target bare repo
- Updates saved push config on success

Arguments:

- --repo-path, -r (default: .)
- --host, -h
- --port, -p (default: 22)
- --username, -u
- --key, -k
- --password
- --deploy-path, -d (default: repos base path)
- --interactive/--no-interactive (default: interactive)
- --use-config/--no-use-config (default: no-use-config)
- --dry-run

### deploy pull

Operation:

- Connects to target repository
- Optionally commits/syncs remote working tree
- Pulls remote changes into local repo
- Updates saved pull config on success

Arguments:

- push arguments, plus:
- --commit/--no-commit
- --sync-remote/--no-sync-remote
- --branch, -b

### deploy docker-push

Operation:

- Resolves target architecture
- Pulls/saves local image tar for target platform
- Transfers tar to target
- Loads image on target
- Updates saved docker-push config on success

Arguments:

- --image, -i (required)
- --host, -h
- --port, -p (default: 22)
- --username, -u
- --key, -k
- --password
- --platform
- --registry-username
- --registry-password
- --interactive/--no-interactive (default: interactive)
- --use-config/--no-use-config (default: no-use-config)
- --dry-run

### deploy show-config

Operation:

- Prints saved command argument sets

Arguments:

- none

### deploy clear-config

Operation:

- Clears all saved config or one command section

Arguments:

- --command, -c (choices: push, pull)

## Proxy command group

### deploy proxy up

Operation:

- Ensures proxy prerequisites
- Optionally migrates native Caddy
- Starts or reconciles docker-caddy-proxy
- Persists proxy connection args

Arguments:

- --host, -h
- --port, -p (default: 22)
- --username, -u
- --key, -k
- --password
- --use-config/--no-use-config (default: use-config)
- --migrate-native-caddy/--no-migrate-native-caddy (default: migrate)
- --ingress-network (repeatable)
- --interactive/--no-interactive (default: interactive)

### deploy proxy status

Operation:

- Shows proxy container status and health URL

Arguments:

- --host, -h
- --port, -p (default: 22)
- --username, -u
- --key, -k
- --password
- --use-config/--no-use-config (default: use-config)

### deploy proxy down

Operation:

- Stops proxy stack

Arguments:

- Same as proxy status

### deploy proxy logs

Operation:

- Shows recent proxy logs

Arguments:

- proxy status arguments
- --lines (default: 80)

### deploy proxy diagnose

Operation:

- Prints proxy status, logs, generated Caddyfile, bootstrap Caddyfile, and native Caddy diagnostics

Arguments:

- proxy status arguments
- --lines (default: 80)

## Service command group

### deploy svc init

Operation:

- Detects FastAPI entrypoint
- Generates Dockerfile and docker-compose.yml
- Writes service skill file

Arguments:

- --domain, -d (required unless --internal)
- --name, -n
- --port
- --image, -i
- --ingress-network (repeatable)
- --global-ingress/--no-global-ingress (default: no-global-ingress)
- --path-prefix
- --internal
- --force

### deploy svc up

Operation:

- Loads service routing/build intent from local docker-compose.yml
- Ensures proxy is running
- Verifies image exists on target (no implicit push/build)
- Uploads compose and metadata
- Starts service containers
- Persists service connection args

Arguments:

- --name, -n
- --host, -h
- --ssh-port (default: 22)
- --username, -u
- --key, -k
- --password
- --use-config/--no-use-config (default: use-config)

## Image command group

### deploy image push

Operation:

- Validates image exists locally
- Transfers image to target and loads it
- Persists image-push connection args

Arguments:

- --image, -i (required)
- --platform
- --registry-username
- --registry-password
- --host, -h
- --ssh-port (default: 22)
- --username, -u
- --key, -k
- --password
- --use-config/--no-use-config (default: use-config)
- --interactive/--no-interactive (default: interactive)

### deploy image build-remote

Operation:

- Validates local git repository
- Syncs repository to target via deploy push
- Verifies remote revision
- Builds image on target from synced context
- Persists image-build-remote connection args

Arguments:

- --image, -i (required)
- --deploy-path
- --host, -h
- --ssh-port (default: 22)
- --username, -u
- --key, -k
- --password
- --use-config/--no-use-config (default: use-config)
- --interactive/--no-interactive (default: interactive)

### deploy svc status

Operation:

- Shows service runtime status, route info, and recent logs

Arguments:

- --name, -n
- --host, -h
- --port, -p (default: 22)
- --username, -u
- --key, -k
- --password
- --use-config/--no-use-config (default: use-config)

### deploy svc down

Operation:

- Stops/removes service containers for one service

Arguments:

- Same as svc status

## Monitor command

### deploy monitor

Operation:

- Starts TUI monitor for proxy/services/networks/resources
- Persists monitor connection args

Arguments:

- --host
- --port (default: 22)
- --username
- --key
- --password
- --use-config/--no-use-config (default: use-config)
- --refresh-interval (default: 5)
- --log-lines (default: 120)
- --command-timeout (default: 10.0)
- --action-timeout (default: 15.0)

## 4) Notes for Future Commands

To keep behavior consistent:

1. Reuse shared connection resolution in deploy/session.py.
2. Keep option names aligned with existing groups (host/port/username/key/password/use-config/interactive).
3. Document defaults in Click decorators and this file together.
4. Add new command arguments to this reference in both sections:
   - Argument Catalog
   - Command Catalog
