# CLI Reference

This document reflects the current command contract defined in REQUIREMENT.md.

## Global Flags

```sh
deploy --non-interactive <group> <command> [...options]
```

- `--non-interactive`: Disable prompts and fail when required values cannot be resolved from CLI or config.
- Configuration is stored in `.deploy/config.yml`.
- Configuration uses nested sections such as `repo.push`, `image.push`, `proxy.up`, and `svc.init`.
- Unknown keys in `.deploy/config.yml` are treated as errors.
- Path-like config values such as `key` and `path` expand `~` on load.

## Repository Commands

### Push

```sh
deploy repo push --remote <host> --port 22 --username <user> --key <ssh_key> --path ~/.deploy/repos
```

Useful options:
- `--remote`: Target host. Use `localhost` to run locally.
- `--path`: Remote deploy base path. Defaults to `~/.deploy/repos`.
- `--repo-path`: Local repository path. Defaults to `.`.
- `--use-config/--no-use-config`: Load saved values from `.deploy/config.yml`.

Saved config section:
- `repo.push`

### Pull

```sh
deploy repo pull --remote <host> --port 22 --username <user> --key <ssh_key> --path ~/.deploy/repos
```

Useful options:
- `--remote`: Target host. Use `localhost` to run locally.
- `--path`: Remote deploy base path. Defaults to `~/.deploy/repos`.
- `--repo-path`: Local repository path. Defaults to `.`.
- `--branch`: Pull into a specific local branch.
- `--use-config/--no-use-config`: Load saved values from `.deploy/config.yml`.

Saved config section:
- `repo.pull`

## Image Commands

### Push

```sh
deploy image push --image <image:tag> --remote <host> --username <user> --key <ssh_key>
```

Useful options:
- `--platform`: Override target platform.
- `--registry-username`: Registry username for private images.
- `--registry-password`: Registry password for private images.
- `--use-config/--no-use-config`: Load saved values from `.deploy/config.yml`.

Saved config section:
- `image.push`

### Build

```sh
deploy image build --tag <image:tag> --remote <host> --username <user> --key <ssh_key>
```

Useful options:
- `--tag`: Tag to assign to the built image.
- `--path`: Remote deploy path used for the repo sync step.
- `--use-config/--no-use-config`: Load saved values from `.deploy/config.yml`.

Saved config section:
- `image.build`

## Proxy Commands

### Up

```sh
deploy proxy up --remote <host> --username <user> --key <ssh_key> --network ingress --bootstrap
```

Useful options:
- `--network`: Ingress network name. Repeat for multiple networks.
- `--bootstrap/--no-bootstrap`: Enable or disable native Caddy bootstrap handoff.
- `--use-config/--no-use-config`: Load saved values from `.deploy/config.yml`.

Saved config section:
- `proxy.up`

### Status

```sh
deploy proxy status
```

### Logs

```sh
deploy proxy logs --lines 80
```

### Down

```sh
deploy proxy down
```

These commands resolve target connection settings through saved config when available.

## Service Commands

### Init

```sh
deploy svc init --domain api.example.com --name api --image repo/api:latest --port 8000 --network ingress --global --path-prefix /api
```

Useful options:
- `--image`: Required. Resolution order is CLI, then `svc.init` config, then prompt when interactive.
- `--network`: External ingress network. Repeat for multiple networks.
- `--global`: Join every configured ingress network.
- `--path-prefix`: Route only traffic under this path prefix.
- When `--domain` is not specified, the service is internal-only with no public routing.
- `--force`: Overwrite existing generated files.

Saved config section:
- `svc.init`

### Up

```sh
deploy svc up --name api --remote <host> --username <user> --key <ssh_key>
```

Saved config section:
- `svc.up`

### Status

```sh
deploy svc status --name api
```

### Down

```sh
deploy svc down --name api
```

These commands resolve target connection settings through saved config when available.
