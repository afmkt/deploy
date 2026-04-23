# Repository Knowledge

## Project Structure
- `deploy/service.py` - ServiceManager class for remote Docker service lifecycle management
- `deploy/service_deploy_flow.py` - Deployment workflow CLI commands
- `deploy/diagnostic.py` - Diagnostic tools for deployment connectivity issues
- `deploy/docker-compose.yml` - Main compose file for the deploy CLI tool itself
- `~/.deploy/repos/*/docker-compose.yml` - Per-service compose files with Caddy labels

## Caddy Labels Pattern
Services use Caddy Docker labels for automatic routing via `caddy-docker-proxy`:
- `caddy: http://localhost` - Host for the service
- `caddy.handle_path: /service/*` - Path-based routing (recommended for non-internal services)
- `caddy.handle_path.reverse_proxy: "{{upstreams port}}"` - Reverse proxy configuration
- `caddy.handle_path.reverse_proxy.header_up: "X-forwarded-Prefix /service"` - Header configuration
- `deploy.scope: single` or `deploy.scope: global` - Deployment scope

## Key Methods Added
- `ServiceManager.get_path_prefix(service_name)` - Extracts path prefix from `caddy.handle_path` label via `docker inspect`