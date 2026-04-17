"""Shared filesystem layout constants for deployment artifacts."""

DEPLOY_BASE_DIR = "~/.deploy"
REPOS_DIR = f"{DEPLOY_BASE_DIR}/repos"
SERVICES_DIR = f"{DEPLOY_BASE_DIR}/services"
PROXY_DIR = f"{REPOS_DIR}/docker-caddy-proxy.service"


def get_bare_repo_path(repo_name: str, base_path: str = REPOS_DIR) -> str:
    """Get the full path for a bare repository.

    Args:
        repo_name: Repository name
        base_path: Base deployment path (default: REPOS_DIR)

    Returns:
        Full path to the bare repository (e.g., ~/.deploy/repos/myapp.git)
    """
    return f"{base_path}/{repo_name}.git"


def get_work_dir_path(repo_name: str, base_path: str = REPOS_DIR) -> str:
    """Get the full path for a work directory (git working tree).

    Args:
        repo_name: Repository name
        base_path: Base deployment path (default: REPOS_DIR)

    Returns:
        Full path to the working directory (e.g., ~/.deploy/repos/myapp.work)
    """
    return f"{base_path}/{repo_name}.work"


def get_service_dir_path(repo_name: str, base_path: str = REPOS_DIR) -> str:
    """Get the full path for a service directory.

    Args:
        repo_name: Repository name
        base_path: Base deployment path (default: REPOS_DIR)

    Returns:
        Full path to the service directory (e.g., ~/.deploy/repos/myapp.service)
    """
    return f"{base_path}/{repo_name}.service"