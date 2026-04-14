"""Git SSH Deploy Tool - Main CLI entry point."""

import sys
from pathlib import Path
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from deploy.git import GitRepository
from deploy.ssh import SSHConnection
from deploy.remote import RemoteServer
from deploy.caddy import CaddyManager
from deploy.docker import DockerManager, _safe_image_filename
from deploy.proxy import (
    ProxyManager,
    PROXY_IMAGE,
    INGRESS_NETWORK,
    normalize_ingress_networks,
)
from deploy.service import (
    ServiceManager,
    detect_fastapi_entrypoint,
    render_dockerfile,
    render_service_compose,
)
from deploy.config import DeployConfig
from deploy.utils import (
    prompt_connection_details,
    prompt_deploy_path,
    print_summary,
)
from deploy import __version__

console = Console()


@click.command()
@click.option("--repo-path", "-r", default=".", help="Path to local Git repository")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password (not recommended, use key instead)")
@click.option("--deploy-path", "-d", default="/var/repos", help="Deploy path on remote server")
@click.option("--interactive/--no-interactive", default=True, help="Interactive mode")
@click.option("--use-config/--no-use-config", default=False, help="Load arguments from config file")
@click.option("--dry-run", is_flag=True, help="Validate connection and arguments without performing actual push")
def main(repo_path: str, host: str, port: int, username: str, key: str,
         password: str, deploy_path: str, interactive: bool, use_config: bool, dry_run: bool):
    """Git SSH Deploy Tool - Sync local Git repository to remote server over SSH.

    This tool automates repository setup, remote configuration, and deployment
    in a single command.
    """
    # Display banner
    console.print(Panel.fit(
        "[bold blue]Git SSH Deploy Tool[/bold blue]\n"
        "Sync local Git repository to remote server over SSH",
        border_style="blue"
    ))

    # Load from config if requested
    config = DeployConfig()
    if use_config:
        saved_args = config.load_args("push")
        if saved_args:
            console.print("[dim]Loading arguments from config...[/dim]")
            # Only use saved args if not explicitly provided via CLI
            if not host and "host" in saved_args:
                host = saved_args["host"]
            if port == 22 and "port" in saved_args:
                port = saved_args["port"]
            if not username and "username" in saved_args:
                username = saved_args["username"]
            if not key and "key" in saved_args:
                key = saved_args["key"]
            if deploy_path == "/var/repos" and "deploy_path" in saved_args:
                deploy_path = saved_args["deploy_path"]
            if repo_path == "." and "repo_path" in saved_args:
                repo_path = saved_args["repo_path"]

    # Validate local Git repository
    console.print("\n[bold]Step 1: Validating local repository[/bold]")
    repo = GitRepository(repo_path)
    if not repo.validate():
        sys.exit(1)

    repo_name = repo.get_repo_name()
    console.print(f"[green]Repository name: {repo_name}[/green]")

    # Get connection details
    console.print("\n[bold]Step 2: Configuring SSH connection[/bold]")
    if interactive and not host:
        conn_details = prompt_connection_details()
        host = conn_details["host"]
        port = conn_details["port"]
        username = conn_details["username"]
        key = conn_details["key_filename"]
        password = conn_details["password"]
    else:
        # Non-interactive mode - require host and username
        if not host:
            console.print("[red]✗ Host is required[/red]")
            sys.exit(1)
        if not username:
            console.print("[red]✗ Username is required[/red]")
            sys.exit(1)

    # Get deployment path
    if interactive and deploy_path == "/var/repos":
        deploy_path = prompt_deploy_path()

    # Save arguments to config
    args_to_save = {
        "repo_path": repo_path,
        "host": host,
        "port": port,
        "username": username,
        "key": key,
        "deploy_path": deploy_path,
    }
    config.save_args(args_to_save, "push")
    console.print(f"[dim]Arguments saved to {config.get_config_path()}[/dim]")

    # Connect to remote server
    console.print("\n[bold]Step 3: Connecting to remote server[/bold]")
    ssh = SSHConnection(
        host=host,
        port=port,
        username=username,
        password=password,
        key_filename=key,
    )

    if not ssh.connect():
        sys.exit(1)

    if dry_run:
        console.print("\n[green]✓ Dry run completed successfully - connection and arguments are valid[/green]")
        ssh.disconnect()
        return

    try:
        # Setup remote deployment
        console.print("\n[bold]Step 4: Setting up remote deployment[/bold]")
        remote = RemoteServer(ssh, deploy_path)
        current_branch = repo.get_current_branch() or "main"
        success, bare_repo_url = remote.setup_deployment(repo_name, current_branch)

        if not success:
            console.print("[red]✗ Failed to setup remote deployment[/red]")
            sys.exit(1)

        # Add remote to local repository
        console.print("\n[bold]Step 5: Configuring local remote[/bold]")
        remote_name = "deploy"
        if not repo.add_remote(remote_name, bare_repo_url):
            console.print("[red]✗ Failed to add remote[/red]")
            sys.exit(1)

        # Push to remote
        console.print("\n[bold]Step 6: Pushing to remote[/bold]")
        if not repo.push(remote_name):
            console.print("[red]✗ Failed to push to remote[/red]")
            sys.exit(1)

        # Update remote working directory
        console.print("\n[bold]Step 7: Updating remote working directory[/bold]")
        bare_repo_path = remote.get_bare_repo_path(repo_name)
        working_dir_path = remote.get_working_dir_path(repo_name)
        current_branch = repo.get_current_branch() or "main"
        if not remote.clone_or_update_working_dir(bare_repo_path, working_dir_path, current_branch):
            console.print("[red]✗ Failed to update remote working directory[/red]")
            sys.exit(1)

        # Get revision information
        local_revision = repo.get_current_revision()
        remote_revision = remote.get_remote_revision(working_dir_path)

        # Print summary
        print_summary(host, repo_name, bare_repo_url, working_dir_path,
                     local_revision=local_revision, remote_revision=remote_revision)

    finally:
        ssh.disconnect()


@click.command()
@click.option("--repo-path", "-r", default=".", help="Path to local Git repository")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password (not recommended, use key instead)")
@click.option("--deploy-path", "-d", default="/var/repos", help="Deploy path on remote server")
@click.option("--interactive/--no-interactive", default=True, help="Interactive mode")
@click.option("--commit/--no-commit", default=False, help="Commit changes in remote working directory")
@click.option("--sync-remote/--no-sync-remote", default=False, help="Check if remote working dir is clean, commit changes, push to bare repo, then pull")
@click.option("--branch", "-b", help="Branch name to pull to")
@click.option("--use-config/--no-use-config", default=False, help="Load arguments from config file")
@click.option("--dry-run", is_flag=True, help="Validate connection and arguments without performing actual pull")
def pull(repo_path: str, host: str, port: int, username: str, key: str,
         password: str, deploy_path: str, interactive: bool, commit: bool,
         sync_remote: bool, branch: str, use_config: bool, dry_run: bool):
    """Pull from remote repository to local.

    This tool pulls changes from the remote repository to the local repository.
    """
    # Display banner
    console.print(Panel.fit(
        "[bold blue]Git SSH Deploy Tool - Pull Mode[/bold blue]\n"
        "Pull changes from remote repository to local",
        border_style="blue"
    ))

    # Load from config if requested
    config = DeployConfig()
    if use_config:
        saved_args = config.load_args("pull")
        if saved_args:
            console.print("[dim]Loading arguments from config...[/dim]")
            # Only use saved args if not explicitly provided via CLI
            if not host and "host" in saved_args:
                host = saved_args["host"]
            if port == 22 and "port" in saved_args:
                port = saved_args["port"]
            if not username and "username" in saved_args:
                username = saved_args["username"]
            if not key and "key" in saved_args:
                key = saved_args["key"]
            if deploy_path == "/var/repos" and "deploy_path" in saved_args:
                deploy_path = saved_args["deploy_path"]
            if repo_path == "." and "repo_path" in saved_args:
                repo_path = saved_args["repo_path"]

    # Validate local Git repository
    console.print("\n[bold]Step 1: Validating local repository[/bold]")
    repo = GitRepository(repo_path)
    if not repo.validate():
        sys.exit(1)

    repo_name = repo.get_repo_name()
    console.print(f"[green]Repository name: {repo_name}[/green]")

    # Get connection details
    console.print("\n[bold]Step 2: Configuring SSH connection[/bold]")
    if interactive and not host:
        conn_details = prompt_connection_details()
        host = conn_details["host"]
        port = conn_details["port"]
        username = conn_details["username"]
        key = conn_details["key_filename"]
        password = conn_details["password"]
    else:
        # Non-interactive mode - require host and username
        if not host:
            console.print("[red]✗ Host is required[/red]")
            sys.exit(1)
        if not username:
            console.print("[red]✗ Username is required[/red]")
            sys.exit(1)

    # Get deployment path
    if interactive and deploy_path == "/var/repos":
        deploy_path = prompt_deploy_path()

    # Save arguments to config
    args_to_save = {
        "repo_path": repo_path,
        "host": host,
        "port": port,
        "username": username,
        "key": key,
        "deploy_path": deploy_path,
    }
    config.save_args(args_to_save, "pull")
    console.print(f"[dim]Arguments saved to {config.get_config_path()}[/dim]")

    # Connect to remote server
    console.print("\n[bold]Step 3: Connecting to remote server[/bold]")
    ssh = SSHConnection(
        host=host,
        port=port,
        username=username,
        password=password,
        key_filename=key,
    )

    if not ssh.connect():
        sys.exit(1)

    if dry_run:
        console.print("\n[green]✓ Dry run completed successfully - connection and arguments are valid[/green]")
        ssh.disconnect()
        return

    try:
        # Get remote paths
        remote = RemoteServer(ssh, deploy_path)
        bare_repo_path = remote.get_bare_repo_path(repo_name)
        working_dir_path = remote.get_working_dir_path(repo_name)

        # Abort early on local dirty state to avoid accidental merge conflicts.
        if repo.has_uncommitted_changes():
            console.print("[red]✗ Local repository has uncommitted changes; commit or stash before pulling[/red]")
            sys.exit(1)

        # Check if bare repository exists
        if not remote.directory_exists(bare_repo_path):
            console.print(f"[red]✗ Remote repository does not exist: {bare_repo_path}[/red]")
            sys.exit(1)

        # Optional: Sync remote working directory (check clean, commit, push, then pull)
        if sync_remote:
            console.print("\n[bold]Step 4: Checking if remote working directory is clean[/bold]")
            has_uncommitted = remote.has_uncommitted_changes(working_dir_path)
            if has_uncommitted is None:
                sys.exit(1)

            has_unpushed = remote.has_unpushed_commits(working_dir_path)
            if has_unpushed is None:
                sys.exit(1)
            
            if has_uncommitted or has_unpushed:
                if has_uncommitted:
                    console.print("[yellow]Remote working directory has uncommitted changes[/yellow]")
                    # Commit changes
                    console.print("\n[bold]Step 5: Committing changes in remote working directory[/bold]")
                    if not remote.commit_remote_changes(working_dir_path):
                        console.print("[red]✗ Failed to commit changes in remote working directory[/red]")
                        sys.exit(1)
                
                if has_unpushed:
                    console.print("[yellow]Remote working directory has unpushed commits[/yellow]")
                
                # Push changes to bare repository
                console.print("\n[bold]Step 6: Pushing changes to bare repository[/bold]")
                if not remote.push_to_bare_repo(working_dir_path):
                    console.print("[red]✗ Failed to push changes to bare repository[/red]")
                    sys.exit(1)
            else:
                console.print("[green]✓ Remote working directory is clean and up to date[/green]")
        
        # Optional: Commit changes in remote working directory (without sync check)
        elif commit:
            console.print("\n[bold]Step 4: Committing changes in remote working directory[/bold]")
            if not remote.commit_remote_changes(working_dir_path):
                console.print("[red]✗ Failed to commit changes in remote working directory[/red]")
                sys.exit(1)

            # Push changes to bare repository
            console.print("\n[bold]Step 5: Pushing changes to bare repository[/bold]")
            if not remote.push_to_bare_repo(working_dir_path):
                console.print("[red]✗ Failed to push changes to bare repository[/red]")
                sys.exit(1)

        # Pull from remote to local (default action)
        step_num = 7 if sync_remote else 6
        console.print(f"\n[bold]Step {step_num}: Pulling from remote to local[/bold]")
        # Add remote if not exists
        remote_name = "deploy"
        bare_repo_url = f"ssh://{username}@{host}:{port}{bare_repo_path}"
        if not repo.add_remote(remote_name, bare_repo_url):
            console.print("[red]✗ Failed to add remote[/red]")
            sys.exit(1)

        # Checkout branch if specified
        if branch:
            if not repo.checkout_branch(branch, create=True):
                console.print(f"[red]✗ Failed to checkout branch: {branch}[/red]")
                sys.exit(1)

        # Pull from remote
        if not repo.pull(remote_name):
            console.print("[red]✗ Failed to pull from remote[/red]")
            sys.exit(1)

        # Get revision information
        local_revision = repo.get_current_revision()
        remote_revision = remote.get_remote_revision(working_dir_path)
        console.print("\n[green]✓ Pull operation completed successfully[/green]")
        console.print(f"\n[bold]Revision Info:[/bold]")
        console.print(f"  Local: {local_revision or 'unknown'}")
        console.print(f"  Remote: {remote_revision or 'unknown'}")

    finally:
        ssh.disconnect()


@click.command()
def show_config():
    """Show saved configuration."""
    config = DeployConfig()
    config_data = config.load_config()
    
    if not config_data:
        console.print("[yellow]No saved configuration found.[/yellow]")
        return
    
    console.print(Panel.fit(
        "[bold blue]Saved Configuration[/bold blue]",
        border_style="blue"
    ))
    
    for command, args in config_data.items():
        console.print(f"\n[bold]{command.upper()}[/bold]")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Argument", style="cyan")
        table.add_column("Value", style="green")
        
        for key, value in args.items():
            table.add_row(key, str(value))
        
        console.print(table)
    
    console.print(f"\n[dim]Config file: {config.get_config_path()}[/dim]")


@click.command()
@click.option("--command", "-c", type=click.Choice(["push", "pull", "caddy"]), help="Clear config for specific command only")
def clear_config(command: str):
    """Clear saved configuration."""
    config = DeployConfig()
    
    if command:
        config.clear_config(command)
        console.print(f"[green]✓ Cleared {command} configuration[/green]")
    else:
        config.clear_config()
        console.print("[green]✓ Cleared all configuration[/green]")


@click.command()
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password (not recommended, use key instead)")
@click.option("--template", "-t", default="default", help="Caddy template name (default, with_tls)")
@click.option("--interactive/--no-interactive", default=True, help="Interactive mode")
@click.option("--use-config/--no-use-config", default=False, help="Load arguments from config file")
@click.option("--dry-run", is_flag=True, help="Validate connection and configuration without making changes")
@click.option("--template-dir", help="Import remote Caddyfile entries to local directory")
@click.option("--apply", "apply_path", default=None, help="Apply template file or directory to remote server")
@click.option("--force", is_flag=True, help="Skip confirmation prompts")
def caddy(host: str, port: int, username: str, key: str, password: str,
          template: str, interactive: bool, use_config: bool, dry_run: bool,
          template_dir: str, apply_path: str, force: bool):
    """Setup and configure Caddy reverse proxy on remote server.
    
    This command helps you install Caddy, analyze existing configuration,
    and add new reverse proxy entries.
    
    Import mode (--template-dir):
        Import remote Caddyfile entries to local directory.
        Creates caddy.entry/{remote_address}/{domain}-{port}.caddy files.
    
    Apply mode (--apply):
        Apply template file or directory to remote server.
        Single file: Append or update entry in remote Caddyfile.
        Directory: Replace entire remote Caddyfile with all templates.
    """
    # Display banner
    console.print(Panel.fit(
        "[bold blue]Git SSH Deploy Tool - Caddy Setup[/bold blue]\n"
        "Configure Caddy reverse proxy on remote server",
        border_style="blue"
    ))

    # Load from config if requested
    config = DeployConfig()
    if use_config:
        saved_args = config.load_args("caddy")
        if saved_args:
            console.print("[dim]Loading arguments from config...[/dim]")
            # Only use saved args if not explicitly provided via CLI
            if not host and "host" in saved_args:
                host = saved_args["host"]
            if port == 22 and "port" in saved_args:
                port = saved_args["port"]
            if not username and "username" in saved_args:
                username = saved_args["username"]
            if not key and "key" in saved_args:
                key = saved_args["key"]
            if template == "default" and "template" in saved_args:
                template = saved_args["template"]

    # Get connection details
    console.print("\n[bold]Step 1: Configuring SSH connection[/bold]")
    if interactive and not host:
        conn_details = prompt_connection_details()
        host = conn_details["host"]
        port = conn_details["port"]
        username = conn_details["username"]
        key = conn_details["key_filename"]
        password = conn_details["password"]
    else:
        # Non-interactive mode - require host and username
        if not host:
            console.print("[red]✗ Host is required[/red]")
            sys.exit(1)
        if not username:
            console.print("[red]✗ Username is required[/red]")
            sys.exit(1)

    # Save arguments to config
    args_to_save = {
        "host": host,
        "port": port,
        "username": username,
        "key": key,
        "template": template,
    }
    config.save_args(args_to_save, "caddy")
    console.print(f"[dim]Arguments saved to {config.get_config_path()}[/dim]")

    # Connect to remote server
    console.print("\n[bold]Step 2: Connecting to remote server[/bold]")
    ssh = SSHConnection(
        host=host,
        port=port,
        username=username,
        password=password,
        key_filename=key,
    )

    if not ssh.connect():
        sys.exit(1)

    # Initialize Caddy manager
    caddy_mgr = CaddyManager(ssh)

    # Handle import operation
    if template_dir:
        console.print("\n[bold]Importing remote Caddyfile entries[/bold]")
        result = caddy_mgr.import_remote_config(template_dir, host, force)
        
        # Show summary
        console.print("\n[bold]Import Summary:[/bold]")
        console.print(f"  Imported: {len(result['imported'])} template(s)")
        console.print(f"  Skipped: {len(result['skipped'])} template(s)")
        console.print(f"  Errors: {len(result['errors'])}")
        
        if result['imported']:
            console.print("\n[bold]Imported templates:[/bold]")
            for template_path in result['imported']:
                console.print(f"  • {template_path}")
        
        if result['errors']:
            console.print("\n[bold red]Errors:[/bold red]")
            for error in result['errors']:
                console.print(f"  • {error}")
        
        ssh.disconnect()
        return

    # Handle apply operation
    if apply_path:
        console.print("\n[bold]Applying template to remote server[/bold]")
        success = caddy_mgr.apply_template(apply_path, force)
        
        if success:
            # Validate configuration
            console.print("\n[bold]Validating configuration[/bold]")
            if caddy_mgr.validate_config():
                # Reload Caddy
                console.print("\n[bold]Reloading Caddy[/bold]")
                caddy_mgr.reload_caddy()
                console.print("\n[bold green]✓ Template applied successfully![/bold green]")
            else:
                console.print("\n[red]✗ Configuration validation failed[/red]")
                sys.exit(1)
        else:
            console.print("\n[red]✗ Failed to apply template[/red]")
            sys.exit(1)
        
        ssh.disconnect()
        return

    if dry_run:
        console.print("\n[bold]Dry Run Analysis[/bold]")
        console.print("=" * 50)
        
        # Check Caddy installation status
        console.print("\n[bold]1. Caddy Installation Status[/bold]")
        if caddy_mgr.is_caddy_installed():
            version = caddy_mgr.get_caddy_version()
            console.print(f"  [green]✓ Caddy is installed (version: {version})[/green]")
            console.print("  [dim]No installation needed[/dim]")
        else:
            console.print("  [yellow]⚠ Caddy is not installed[/yellow]")
            console.print("  [dim]Will install Caddy if run without --dry-run[/dim]")
            os_type = caddy_mgr.detect_os()
            if os_type:
                console.print(f"  [dim]Detected OS: {os_type}[/dim]")
            else:
                console.print("  [dim]Could not detect OS type[/dim]")
        
        # Analyze current configuration
        console.print("\n[bold]2. Current Caddy Configuration[/bold]")
        config_content = caddy_mgr.read_caddy_config()
        if config_content:
            config_path = caddy_mgr.get_caddy_config_path()
            console.print(f"  Config file: {config_path}")
            
            # Parse detailed configuration
            detailed_config = caddy_mgr.parse_detailed_config(config_content)
            
            # Show domain configurations
            if detailed_config['domains']:
                console.print(f"\n  [bold]Domain-based Services ({len(detailed_config['domains'])} configured):[/bold]")
                for i, domain_info in enumerate(detailed_config['domains'], 1):
                    console.print(f"\n  [bold]{i}. {domain_info['domain']}[/bold]")
                    
                    # Show public port
                    if domain_info.get('public_port'):
                        console.print(f"     Public port: {domain_info['public_port']}")
                    
                    # Show backend ports
                    if domain_info['ports']:
                        console.print(f"     Backend ports: {', '.join(domain_info['ports'])}")
                    
                    # Show paths
                    if domain_info['paths']:
                        console.print(f"     Paths: {', '.join(domain_info['paths'])}")
                    
                    # Show services
                    if domain_info['services']:
                        console.print(f"     Services:")
                        for service in domain_info['services']:
                            console.print(f"       - {service}")
                    
                    # Show raw config (indented)
                    if domain_info['config_lines']:
                        console.print(f"     Configuration:")
                        for line in domain_info['config_lines'][:5]:  # Show first 5 lines
                            console.print(f"       {line}")
                        if len(domain_info['config_lines']) > 5:
                            console.print(f"       ... ({len(domain_info['config_lines']) - 5} more lines)")
            else:
                console.print("\n  [dim]No domain-based services configured[/dim]")
            
            # Show public IP services
            if detailed_config['public_services']:
                console.print(f"\n  [bold]Public IP Services ({len(detailed_config['public_services'])} configured):[/bold]")
                for i, service_info in enumerate(detailed_config['public_services'], 1):
                    # Display full listen address
                    if service_info.get('listen_address'):
                        listen_display = f"{service_info['listen_address']}:{service_info['listen_port']}"
                    else:
                        listen_display = f":{service_info['listen_port']}"
                    console.print(f"\n  [bold]{i}. Listening on {listen_display}[/bold]")
                    
                    # Show backend ports
                    if service_info['ports']:
                        console.print(f"     Backend ports: {', '.join(service_info['ports'])}")
                    
                    # Show paths
                    if service_info['paths']:
                        console.print(f"     Paths: {', '.join(service_info['paths'])}")
                    
                    # Show services
                    if service_info['services']:
                        console.print(f"     Services:")
                        for service in service_info['services']:
                            console.print(f"       - {service}")
                    
                    # Show raw config (indented)
                    if service_info['config_lines']:
                        console.print(f"     Configuration:")
                        for line in service_info['config_lines'][:5]:  # Show first 5 lines
                            console.print(f"       {line}")
                        if len(service_info['config_lines']) > 5:
                            console.print(f"       ... ({len(service_info['config_lines']) - 5} more lines)")
            else:
                console.print("\n  [dim]No public IP services configured[/dim]")
            
            # Show summary of all domains and ports
            parsed = caddy_mgr.parse_domains_and_ports(config_content)
            console.print(f"\n  [bold]Summary:[/bold]")
            console.print(f"    Total domains: {len(parsed['domains'])}")
            console.print(f"    Total public services: {len(detailed_config['public_services'])}")
            console.print(f"    Total backend ports: {len(parsed['ports'])}")
            if parsed['domains']:
                console.print(f"    Domains: {', '.join(parsed['domains'])}")
            if detailed_config['public_services']:
                public_listeners = []
                for s in detailed_config['public_services']:
                    if s.get('listen_address'):
                        public_listeners.append(f"{s['listen_address']}:{s['listen_port']}")
                    else:
                        public_listeners.append(f":{s['listen_port']}")
                console.print(f"    Public listeners: {', '.join(public_listeners)}")
            if parsed['ports']:
                console.print(f"    Backend ports: {', '.join(parsed['ports'])}")
        else:
            console.print("  [yellow]Could not read Caddy configuration[/yellow]")
            console.print("  [dim]Configuration file may not exist yet[/dim]")
        
        # Show available templates
        console.print("\n[bold]3. Available Templates[/bold]")
        available_templates = caddy_mgr.get_available_templates()
        if available_templates:
            for tmpl in available_templates:
                console.print(f"  • {tmpl}")
        else:
            console.print("  [dim]No templates found[/dim]")
        
        console.print("\n[green]✓ Dry run completed successfully[/green]")
        ssh.disconnect()
        return

    try:

        # Check/Install Caddy
        console.print("\n[bold]Step 3: Checking Caddy installation[/bold]")
        if not caddy_mgr.is_caddy_installed():
            console.print("[yellow]Caddy is not installed[/yellow]")
            if interactive:
                from rich.prompt import Confirm
                install = Confirm.ask("Install Caddy now?", default=True)
                if not install:
                    console.print("[yellow]Caddy installation skipped[/yellow]")
                    return
            
            if not caddy_mgr.install_caddy():
                console.print("[red]✗ Failed to install Caddy[/red]")
                sys.exit(1)
        else:
            version = caddy_mgr.get_caddy_version()
            console.print(f"[green]✓ Caddy is installed (version: {version})[/green]")

        # Analyze current configuration
        console.print("\n[bold]Step 4: Analyzing current configuration[/bold]")
        config_content = caddy_mgr.read_caddy_config()
        if config_content:
            parsed = caddy_mgr.parse_domains_and_ports(config_content)
            
            # Display current domains
            if parsed["domains"]:
                console.print("\n[bold]Current domains:[/bold]")
                for domain in parsed["domains"]:
                    console.print(f"  • {domain}")
            else:
                console.print("[dim]No domains configured yet[/dim]")
            
            # Display current ports
            if parsed["ports"]:
                console.print("\n[bold]Current ports:[/bold]")
                for port_num in parsed["ports"]:
                    console.print(f"  • {port_num}")
            else:
                console.print("[dim]No ports configured yet[/dim]")
        else:
            console.print("[yellow]Could not read Caddy configuration[/yellow]")

        # Import remote configuration to local directory
        console.print("\n[bold]Step 5: Importing remote configuration[/bold]")
        import_result = caddy_mgr.import_remote_config(".", host, force)
        
        # Show import summary
        if import_result['imported']:
            console.print(f"[green]✓ Imported {len(import_result['imported'])} template(s) from remote[/green]")
        elif import_result['errors']:
            console.print(f"[yellow]⚠ Could not import from remote: {import_result['errors'][0]}[/yellow]")
            console.print("[dim]Using local templates instead[/dim]")
        
        # If --use-config is specified, exit after importing
        if use_config:
            console.print("\n[bold green]✓ Import complete![/bold green]")
            ssh.disconnect()
            return
        
        # Get user input for new entry
        console.print("\n[bold]Step 6: Configure new entry[/bold]")
        
        # Show available templates (now includes imported ones)
        available_templates = caddy_mgr.get_available_templates()
        if available_templates:
            console.print(f"\n[bold]Available templates:[/bold] {', '.join(available_templates)}")
        
        if interactive:
            from rich.prompt import Prompt
            
            # Prompt for domain
            domain = Prompt.ask("Domain name")
            if not domain:
                console.print("[red]✗ Domain name is required[/red]")
                sys.exit(1)
            
            # Prompt for port
            port_input = Prompt.ask("Port number")
            try:
                port_num = int(port_input)
                if not (1 <= port_num <= 65535):
                    raise ValueError
            except ValueError:
                console.print("[red]✗ Invalid port number[/red]")
                sys.exit(1)
            
            # Prompt for template if not specified
            if template == "default" and available_templates:
                template_input = Prompt.ask(
                    "Template name",
                    default=template,
                    choices=available_templates
                )
                if template_input:
                    template = template_input
        else:
            # Non-interactive mode - domain and port must be provided via prompts
            console.print("[yellow]⚠ Non-interactive mode requires domain and port to be provided interactively[/yellow]")
            from rich.prompt import Prompt
            
            domain = Prompt.ask("Domain name")
            if not domain:
                console.print("[red]✗ Domain name is required[/red]")
                sys.exit(1)
            
            port_input = Prompt.ask("Port number")
            try:
                port_num = int(port_input)
                if not (1 <= port_num <= 65535):
                    raise ValueError
            except ValueError:
                console.print("[red]✗ Invalid port number[/red]")
                sys.exit(1)

        # Check if entry already exists
        if caddy_mgr.entry_exists(domain):
            console.print(f"[yellow]⚠ Entry for {domain} already exists in Caddyfile[/yellow]")
            if interactive:
                from rich.prompt import Confirm
                overwrite = Confirm.ask("Overwrite existing entry?", default=False)
                if not overwrite:
                    console.print("[yellow]Operation cancelled[/yellow]")
                    return

        # Add entry
        console.print("\n[bold]Step 7: Adding Caddy entry[/bold]")
        if not caddy_mgr.add_entry(domain, port_num, template):
            console.print("[red]✗ Failed to add entry[/red]")
            sys.exit(1)

        # Validate configuration
        console.print("\n[bold]Step 8: Validating configuration[/bold]")
        if not caddy_mgr.validate_config():
            console.print("[red]✗ Configuration validation failed[/red]")
            sys.exit(1)

        # Reload Caddy
        console.print("\n[bold]Step 9: Reloading Caddy[/bold]")
        caddy_mgr.reload_caddy()

        # Print summary
        console.print("\n[bold green]✓ Caddy configuration complete![/bold green]")
        console.print(f"\n[bold]Summary:[/bold]")
        console.print(f"  Domain: {domain}")
        console.print(f"  Port: {port_num}")
        console.print(f"  Template: {template}")
        console.print(f"  Config: {caddy_mgr.get_caddy_config_path()}")

    finally:
        ssh.disconnect()


@click.command()
@click.option("--image", "-i", required=True, help="Docker image to push (name:tag)")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password (not recommended, use key instead)")
@click.option("--platform", help="Target platform override (e.g. linux/amd64, linux/arm64)")
@click.option("--registry-username", help="Docker registry username for private images")
@click.option("--registry-password", help="Docker registry password for private images")
@click.option("--interactive/--no-interactive", default=True, help="Interactive mode")
@click.option("--use-config/--no-use-config", default=False, help="Load arguments from config file")
@click.option("--dry-run", is_flag=True, help="Validate connection without transferring image")
def docker_push(image: str, host: str, port: int, username: str, key: str,
                password: str, platform: str, registry_username: str,
                registry_password: str, interactive: bool, use_config: bool,
                dry_run: bool):
    """Push a Docker image to a remote server over SSH.

    Pulls the image locally (targeting the remote server architecture), saves it
    to a tarball, transfers it via SFTP, and loads it on the remote server.
    """
    import tempfile
    import os

    console.print(Panel.fit(
        "[bold blue]Git SSH Deploy Tool - Docker Push[/bold blue]\n"
        "Transfer a Docker image to a remote server over SSH",
        border_style="blue",
    ))

    # Load saved config if requested
    config = DeployConfig()
    if use_config:
        saved_args = config.load_args("docker-push")
        fallback_sources = ["push", "pull", "caddy"]

        # docker-push uses the same SSH transport as other commands. If its
        # own saved config is incomplete, prefer a complete existing SSH profile
        # instead of mixing partial values from multiple commands.
        if not saved_args.get("host") or not saved_args.get("username") or not saved_args.get("key"):
            if saved_args:
                console.print(
                    "[yellow]docker-push config is incomplete; trying SSH settings from push/pull/caddy.[/yellow]"
                )
            for source in fallback_sources:
                candidate = config.load_args(source)
                if candidate.get("host") and candidate.get("username") and candidate.get("key"):
                    saved_args = candidate
                    console.print(f"[dim]Loading SSH arguments from '{source}' config...[/dim]")
                    break
        elif saved_args:
            console.print("[dim]Loading arguments from config...[/dim]")

        if saved_args:
            if not host and "host" in saved_args:
                host = saved_args["host"]
            if port == 22 and "port" in saved_args:
                port = saved_args["port"]
            if not username and "username" in saved_args:
                username = saved_args["username"]
            if not key and "key" in saved_args:
                key = saved_args["key"]

    # Connection details
    console.print("\n[bold]Step 1: Configuring SSH connection[/bold]")
    if interactive and not host:
        conn_details = prompt_connection_details()
        host = conn_details["host"]
        port = conn_details["port"]
        username = conn_details["username"]
        key = conn_details["key_filename"]
        password = conn_details["password"]
    else:
        if not host:
            console.print("[red]✗ Host is required[/red]")
            sys.exit(1)
        if not username:
            console.print("[red]✗ Username is required[/red]")
            sys.exit(1)

    # Persist non-sensitive args
    config.save_args({"host": host, "port": port, "username": username, "key": key}, "docker-push")
    console.print(f"[dim]Arguments saved to {config.get_config_path()}[/dim]")

    # SSH connect
    console.print("\n[bold]Step 2: Connecting to remote server[/bold]")
    ssh = SSHConnection(host=host, port=port, username=username,
                        password=password, key_filename=key)
    if not ssh.connect():
        sys.exit(1)

    docker_mgr = DockerManager(ssh)

    if dry_run:
        console.print("\n[bold]Dry Run Analysis[/bold]")
        if docker_mgr.is_docker_installed():
            version = docker_mgr.get_docker_version()
            console.print(f"  [green]✓ Docker is installed on remote (version: {version})[/green]")
        else:
            console.print("  [yellow]⚠ Docker is not installed on remote[/yellow]")
        detected = docker_mgr.detect_remote_arch()
        effective_platform = platform or detected
        console.print(f"  Platform: {effective_platform or 'unknown'}")
        console.print(f"  Image: {image}")
        console.print("\n[green]✓ Dry run completed[/green]")
        ssh.disconnect()
        return

    try:
        # Step 3: Ensure Docker is installed on remote
        console.print("\n[bold]Step 3: Checking Docker on remote server[/bold]")
        if not docker_mgr.is_docker_installed():
            console.print("[yellow]Docker is not installed on the remote server[/yellow]")
            if interactive:
                from rich.prompt import Confirm
                if not Confirm.ask("Install Docker now?", default=True):
                    console.print("[yellow]Docker installation skipped — cannot proceed[/yellow]")
                    sys.exit(1)
            if not docker_mgr.install_docker():
                console.print("[red]✗ Failed to install Docker[/red]")
                sys.exit(1)
        else:
            version = docker_mgr.get_docker_version()
            console.print(f"[green]✓ Docker is installed (version: {version})[/green]")

        # Step 4: Resolve target platform
        console.print("\n[bold]Step 4: Detecting target platform[/bold]")
        if platform:
            console.print(f"[dim]Using user-supplied platform: {platform}[/dim]")
        else:
            platform = docker_mgr.detect_remote_arch()
            if not platform:
                console.print("[red]✗ Could not detect remote architecture[/red]")
                sys.exit(1)

        # Step 5: Registry login (if credentials provided)
        if registry_username and registry_password:
            console.print("\n[bold]Step 5: Authenticating with Docker registry[/bold]")
            if not docker_mgr.registry_login(registry_username, registry_password, image):
                sys.exit(1)

        # Step 6: Pull image locally for the target platform
        step = 6 if (registry_username and registry_password) else 5
        console.print(f"\n[bold]Step {step}: Pulling image locally[/bold]")
        if not docker_mgr.pull_image(image, platform):
            sys.exit(1)

        # Step 7: Save image to local tarball
        step += 1
        console.print(f"\n[bold]Step {step}: Saving image to tarball[/bold]")
        tmpdir = tempfile.mkdtemp(prefix="deploy_docker_")
        tar_filename = _safe_image_filename(image)
        local_tar = os.path.join(tmpdir, tar_filename)
        if not docker_mgr.save_image(image, local_tar, platform):
            sys.exit(1)

        # Step 8: Transfer tarball to remote
        step += 1
        remote_tar = f"/tmp/{tar_filename}"
        console.print(f"\n[bold]Step {step}: Transferring tarball to remote[/bold]")
        if not docker_mgr.transfer_tarball(local_tar, remote_tar):
            sys.exit(1)

        # Step 9: Load on remote
        step += 1
        console.print(f"\n[bold]Step {step}: Loading image on remote server[/bold]")
        if not docker_mgr.load_image(remote_tar, image):
            sys.exit(1)

        # Step 10: Cleanup
        step += 1
        console.print(f"\n[bold]Step {step}: Cleaning up[/bold]")
        docker_mgr.cleanup_remote(remote_tar)
        try:
            os.remove(local_tar)
            os.rmdir(tmpdir)
            console.print("[dim]Cleaned up local tarball[/dim]")
        except OSError:
            pass

        console.print(f"\n[bold green]✓ Docker image '{image}' transferred successfully to {host}[/bold green]")

    finally:
        ssh.disconnect()


# ---------------------------------------------------------------------------
# proxy subcommand group
# ---------------------------------------------------------------------------

def _build_ssh_from_config(config: DeployConfig, section: str,
                           host: str, port: int, username: str,
                           key: str, password: str) -> SSHConnection:
    """Return an SSHConnection, loading missing fields from saved config."""
    if not host or not username or not key:
        saved = config.load_args(section)
        fallback_sources = ["push", "pull", "caddy", "docker-push"]
        if not saved.get("host") or not saved.get("username") or not saved.get("key"):
            for src in fallback_sources:
                candidate = config.load_args(src)
                if candidate.get("host") and candidate.get("username") and candidate.get("key"):
                    saved = candidate
                    break
        host = host or saved.get("host", "")
        port = port if port != 22 else saved.get("port", 22)
        username = username or saved.get("username", "")
        key = key or saved.get("key", "")
    return SSHConnection(host=host, port=port, username=username,
                         password=password, key_filename=key)


@click.group()
def proxy():
    """Manage the caddy-docker-proxy ingress container on the remote server."""
    pass


@proxy.command(name="up")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
@click.option("--migrate-native-caddy/--no-migrate-native-caddy", default=True,
              help="If native Caddy exists, migrate its Caddyfile and stop it before proxy start")
@click.option("--ingress-network", "ingress_networks", multiple=True,
              help="Ingress networks for proxy/service discovery (repeat flag or use comma-separated values)")
def proxy_up(host, port, username, key, password, use_config, migrate_native_caddy, ingress_networks):
    """Start (or ensure running) the caddy-docker-proxy ingress stack."""
    config = DeployConfig()
    ssh = _build_ssh_from_config(config, "proxy", host, port, username, key, password)
    networks = normalize_ingress_networks(ingress_networks)
    if not ssh.host or not ssh.username:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    console.print(Panel.fit(
        "[bold blue]Proxy — up[/bold blue]\n"
        f"Ingress: {PROXY_IMAGE}\n"
        f"Networks: {', '.join(networks)}",
        border_style="blue",
    ))

    if not ssh.connect():
        sys.exit(1)

    try:
        mgr = ProxyManager(ssh)
        from rich.prompt import Confirm

        native_caddy_found = False
        native_caddy_content = None
        should_migrate_native_caddy = False

        # Step 0: detect native Caddy
        console.print("\n[bold]Step 0: Check native Caddy[/bold]")
        native_caddy_found = mgr.native_caddy_exists()
        if native_caddy_found:
            console.print("[yellow]⚠ Native Caddy detected on remote host[/yellow]")
            if migrate_native_caddy:
                should_migrate_native_caddy = Confirm.ask(
                    "Migrate native Caddy config and hand over ports 80/443 to docker-caddy-proxy?",
                    default=True,
                )
            else:
                console.print("[yellow]Native Caddy migration is disabled by flag[/yellow]")
        else:
            console.print("[dim]No native Caddy detected[/dim]")

        # Step 1: ensure network
        console.print("\n[bold]Step 1: Ensure ingress network[/bold]")
        if not mgr.ensure_networks(networks):
            sys.exit(1)

        # Step 2: check image availability
        console.print("\n[bold]Step 2: Check proxy image[/bold]")
        if not mgr.proxy_image_exists_remote():
            console.print(f"[yellow]Image {PROXY_IMAGE} not found on remote.[/yellow]")
            if Confirm.ask(
                f"Push {PROXY_IMAGE} to remote now using docker-push?",
                default=True,
            ):
                from click.testing import CliRunner
                runner = CliRunner()
                result = runner.invoke(docker_push, [
                    "--image", PROXY_IMAGE,
                    "--host", ssh.host,
                    "--port", str(ssh.port),
                    "--username", ssh.username,
                    "--no-interactive",
                    *(["--key", ssh.key_filename] if ssh.key_filename else []),
                ], catch_exceptions=False, standalone_mode=False)
                if result.exit_code != 0:
                    console.print("[red]✗ docker-push failed[/red]")
                    sys.exit(1)
            else:
                console.print(
                    f"[yellow]Run: deploy docker-push -i {PROXY_IMAGE} first[/yellow]"
                )
                sys.exit(1)

        # Step 3: prepare migration bootstrap (if needed)
        console.print("\n[bold]Step 3: Prepare bootstrap Caddyfile[/bold]")
        if should_migrate_native_caddy:
            native_caddy_content = mgr.read_native_caddyfile()
            if native_caddy_content and native_caddy_content.strip():
                native_config_path = mgr.get_native_caddyfile_path()
                console.print(f"[green]✓ Native Caddyfile found at {native_config_path}[/green]")
                if not mgr.backup_native_caddyfile(native_caddy_content):
                    sys.exit(1)

                if mgr.native_config_uses_loopback_upstreams(native_caddy_content):
                    console.print(
                        "[yellow]Detected localhost loopback upstreams. "
                        "Rewriting upstreams to the host-side bridge address for bridge-mode compatibility.[/yellow]"
                    )
                    rewritten_caddy_content = mgr.rewrite_native_caddyfile_for_bridge_mode(
                        native_caddy_content
                    )
                    native_caddy_content = rewritten_caddy_content
                else:
                    rewritten_caddy_content = mgr.rewrite_native_caddyfile_for_bridge_mode(
                        native_caddy_content
                    )
                    if rewritten_caddy_content != native_caddy_content:
                        console.print(
                            "[yellow]Rewrote loopback upstreams for bridge-mode host reachability[/yellow]"
                        )
                    native_caddy_content = rewritten_caddy_content
            else:
                console.print(
                    "[red]✗ Native Caddy was detected, but its config could not be read. "
                    "Refusing to cut over with an empty bootstrap config.[/red]"
                )
                console.print(
                    "[yellow]Check the native Caddy config path and rerun proxy up after fixing it.[/yellow]"
                )
                sys.exit(1)
        else:
            # Always ensure the mounted bootstrap file exists.
            native_caddy_content = ""

        if not mgr.write_bootstrap_caddyfile(native_caddy_content):
            sys.exit(1)

        # Step 4: deploy compose file
        console.print("\n[bold]Step 4: Deploy ingress compose file[/bold]")
        if not mgr.deploy_compose_file(networks):
            sys.exit(1)

        # Step 5: stop native Caddy before binding 80/443
        native_stopped = False
        if should_migrate_native_caddy:
            console.print("\n[bold]Step 5: Stop native Caddy[/bold]")
            if not mgr.stop_native_caddy():
                sys.exit(1)
            native_stopped = True

        # Step 6: bring up
        console.print("\n[bold]Step 6: Start ingress proxy[/bold]")
        if not mgr.up():
            if native_stopped:
                console.print("[yellow]Attempting rollback: restart native Caddy...[/yellow]")
                if mgr.start_native_caddy():
                    console.print("[yellow]Native Caddy restarted after proxy start failure[/yellow]")
            sys.exit(1)

        status = mgr.get_status()
        console.print(f"\n[bold green]✓ Ingress proxy is {status}[/bold green]")
        config.save_args({"host": ssh.host, "port": ssh.port,
                          "username": ssh.username, "key": ssh.key_filename}, "proxy")
    finally:
        ssh.disconnect()


@proxy.command(name="status")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
def proxy_status(host, port, username, key, password, use_config):
    """Show the status of the caddy-docker-proxy container."""
    config = DeployConfig()
    ssh = _build_ssh_from_config(config, "proxy", host, port, username, key, password)
    if not ssh.host or not ssh.username:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    if not ssh.connect():
        sys.exit(1)
    try:
        mgr = ProxyManager(ssh)
        status = mgr.get_status()
        running = mgr.is_running()
        if status:
            colour = "green" if running else "yellow"
            console.print(f"[{colour}]Ingress proxy: {status}[/{colour}]")
        else:
            console.print("[yellow]Ingress proxy container not found[/yellow]")
            console.print("[dim]Run: deploy proxy up[/dim]")
    finally:
        ssh.disconnect()


@proxy.command(name="down")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
def proxy_down(host, port, username, key, password, use_config):
    """Stop the caddy-docker-proxy ingress stack."""
    config = DeployConfig()
    ssh = _build_ssh_from_config(config, "proxy", host, port, username, key, password)
    if not ssh.host or not ssh.username:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    if not ssh.connect():
        sys.exit(1)
    try:
        ProxyManager(ssh).down()
    finally:
        ssh.disconnect()


@proxy.command(name="logs")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
@click.option("--lines", default=80, show_default=True,
              help="How many proxy log lines to fetch")
def proxy_logs(host, port, username, key, password, use_config, lines):
    """Show recent docker-caddy-proxy container logs."""
    config = DeployConfig()
    ssh = _build_ssh_from_config(config, "proxy", host, port, username, key, password)
    if not ssh.host or not ssh.username:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    if not ssh.connect():
        sys.exit(1)
    try:
        logs = ProxyManager(ssh).get_proxy_logs(lines=lines)
        if logs.strip():
            console.print(logs.rstrip())
        else:
            console.print("[yellow]No proxy logs available[/yellow]")
    finally:
        ssh.disconnect()


@proxy.command(name="diagnose")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
@click.option("--lines", default=80, show_default=True,
              help="How many log/journal lines to fetch")
def proxy_diagnose(host, port, username, key, password, use_config, lines):
    """Collect proxy and native Caddy diagnostics from the remote server."""
    config = DeployConfig()
    ssh = _build_ssh_from_config(config, "proxy", host, port, username, key, password)
    if not ssh.host or not ssh.username:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    if not ssh.connect():
        sys.exit(1)

    try:
        mgr = ProxyManager(ssh)

        console.print(Panel.fit(
            "[bold blue]Proxy Diagnose[/bold blue]\n"
            "Remote Caddy migration diagnostics",
            border_style="blue",
        ))

        sections = [
            ("Proxy Status", mgr.get_status() or "not found"),
            ("Proxy Logs", mgr.get_proxy_logs(lines=lines).strip() or "<empty>"),
            (
                "Generated Caddyfile",
                (mgr.get_generated_caddyfile() or "<unavailable>").strip(),
            ),
            (
                "Bootstrap Caddyfile",
                (mgr.get_bootstrap_caddyfile() or "<unavailable>").strip(),
            ),
            (
                "Native Caddyfile Backup",
                (mgr.get_native_caddyfile_backup() or "<unavailable>").strip(),
            ),
            (
                "Native Caddy Status",
                mgr.get_native_caddy_status().strip() or "<empty>",
            ),
            (
                "Native Caddy Journal",
                mgr.get_native_caddy_journal(lines=lines).strip() or "<empty>",
            ),
        ]

        for title, content in sections:
            console.print(f"\n[bold]{title}[/bold]")
            console.print(content)
    finally:
        ssh.disconnect()


# ---------------------------------------------------------------------------
# service subcommand group
# ---------------------------------------------------------------------------

@click.group()
def service():
    """Scaffold and deploy Docker-based services (FastAPI first-class)."""
    pass


@service.command(name="init")
@click.option("--domain", "-d", required=True,
              help="Public domain or hostname for this service (e.g. api.example.com)")
@click.option("--name", "-n", help="Service name (defaults to current directory name)")
@click.option("--port", type=int, help="App port inside container (auto-detected for FastAPI)")
@click.option("--image", "-i",
              help="Use a pre-built image instead of a build directive")
@click.option("--ingress-network", default=INGRESS_NETWORK,
              help="External Docker network used for ingress routing")
@click.option("--force", is_flag=True,
              help="Overwrite existing Dockerfile / docker-compose.yml")
def service_init(domain, name, port, image, ingress_network, force):
    """Scaffold Dockerfile and docker-compose.yml for a FastAPI service.

    Run inside the project directory.  Detects FastAPI entrypoint automatically.
    """
    project_dir = Path(".")

    # Derive service name from directory if not given
    service_name = name or project_dir.resolve().name

    # Detect FastAPI entrypoint
    ep_file, app_str, default_port = detect_fastapi_entrypoint(project_dir)
    effective_port = port or default_port

    console.print(Panel.fit(
        f"[bold blue]Service init — {service_name}[/bold blue]\n"
        f"Domain: {domain}  Port: {effective_port}",
        border_style="blue",
    ))
    console.print(f"[dim]Detected entrypoint: {ep_file} → {app_str}[/dim]")

    # Dockerfile
    dockerfile_path = project_dir / "Dockerfile"
    if dockerfile_path.exists() and not force:
        console.print("[dim]Dockerfile already exists, skipping (use --force to overwrite)[/dim]")
    else:
        dockerfile_path.write_text(render_dockerfile(app_str, effective_port))
        console.print(f"[green]✓ Wrote {dockerfile_path}[/green]")

    # docker-compose.yml
    compose_path = project_dir / "docker-compose.yml"
    if compose_path.exists() and not force:
        console.print("[dim]docker-compose.yml already exists, skipping (use --force to overwrite)[/dim]")
    else:
        compose_content = render_service_compose(
            service_name=service_name,
            domain=domain,
            port=effective_port,
            image=image,
            ingress_network=ingress_network,
        )
        compose_path.write_text(compose_content)
        console.print(f"[green]✓ Wrote {compose_path}[/green]")

    console.print("\n[bold green]✓ Service initialised[/bold green]")
    console.print(f"  Next: [dim]deploy service deploy --host <host> --image <image>[/dim]")


@service.command(name="deploy")
@click.option("--name", "-n", help="Service name (defaults to current directory name)")
@click.option("--image", "-i", required=True,
              help="Docker image to run (must be present on remote or pushed beforehand)")
@click.option("--domain", "-d", required=True,
              help="Public domain / hostname")
@click.option("--port", type=int, default=8000,
              help="App port inside the container")
@click.option("--ingress-network", default=INGRESS_NETWORK,
              help="External Docker network used for ingress routing")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--ssh-port", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
def service_deploy(name, image, domain, port, ingress_network, host, ssh_port, username, key,
                   password, use_config):
    """Deploy a service image to the remote server and register with ingress.

    The image must already exist on the remote (use 'deploy docker-push' first),
    or the command will prompt you to push it.
    """
    config = DeployConfig()
    ssh = _build_ssh_from_config(config, "service", host, ssh_port, username, key, password)
    if not ssh.host or not ssh.username:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    service_name = name or Path(".").resolve().name

    console.print(Panel.fit(
        f"[bold blue]Service deploy — {service_name}[/bold blue]\n"
        f"Image: {image}  Domain: {domain}  Port: {port}\n"
        f"Ingress network: {ingress_network}",
        border_style="blue",
    ))

    if not ssh.connect():
        sys.exit(1)

    try:
        svc_mgr = ServiceManager(ssh)
        proxy_mgr = ProxyManager(ssh)

        # Step 1: check ingress proxy is running
        console.print("\n[bold]Step 1: Check ingress proxy[/bold]")
        if not proxy_mgr.is_running():
            console.print("[yellow]⚠ Ingress proxy is not running[/yellow]")
            console.print("[dim]Run: deploy proxy up[/dim]")
            sys.exit(1)

        # Step 2: check image availability on remote
        console.print("\n[bold]Step 2: Check image on remote[/bold]")
        if not svc_mgr.image_exists_remote(image):
            console.print(f"[yellow]Image '{image}' not found on remote.[/yellow]")
            from rich.prompt import Confirm
            if Confirm.ask(
                f"Push '{image}' to remote now using docker-push?",
                default=True,
            ):
                from click.testing import CliRunner
                runner = CliRunner()
                result = runner.invoke(docker_push, [
                    "--image", image,
                    "--host", ssh.host,
                    "--port", str(ssh.port),
                    "--username", ssh.username,
                    "--no-interactive",
                    *(["--key", ssh.key_filename] if ssh.key_filename else []),
                ], catch_exceptions=False, standalone_mode=False)
                if result.exit_code != 0:
                    console.print("[red]✗ docker-push failed[/red]")
                    sys.exit(1)
                # Reconnect: docker-push opens its own SSH session
                ssh.disconnect()
                if not ssh.connect():
                    sys.exit(1)
                svc_mgr = ServiceManager(ssh)
                proxy_mgr = ProxyManager(ssh)
            else:
                console.print(
                    f"[yellow]Run: deploy docker-push -i {image} --host {ssh.host} first[/yellow]"
                )
                sys.exit(1)
        else:
            console.print(f"[green]✓ Image '{image}' found on remote[/green]")

        # Step 3: ensure service directory and compose file
        console.print("\n[bold]Step 3: Upload service compose[/bold]")
        if not svc_mgr.ensure_service_dir(service_name):
            sys.exit(1)
        compose_content = render_service_compose(
            service_name=service_name,
            domain=domain,
            port=port,
            image=image,
            ingress_network=ingress_network,
        )
        if not svc_mgr.upload_compose(service_name, compose_content):
            sys.exit(1)

        # Step 4: start service
        console.print("\n[bold]Step 4: Start service[/bold]")
        if not svc_mgr.compose_up(service_name):
            sys.exit(1)

        status = svc_mgr.get_status(service_name)
        container_ip = svc_mgr.get_container_ip(service_name)

        console.print(f"\n[bold green]✓ Service '{service_name}' deployed[/bold green]")
        console.print(f"  Domain : {domain}")
        console.print(f"  Status : {status}")
        if container_ip:
            console.print(f"  Container IP: {container_ip}")
        config.save_args({"host": ssh.host, "port": ssh.port,
                          "username": ssh.username, "key": ssh.key_filename}, "service")
    finally:
        ssh.disconnect()


@service.command(name="status")
@click.option("--name", "-n", help="Service name (defaults to current directory name)")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
def service_status(name, host, port, username, key, password, use_config):
    """Show the running status of a deployed service."""
    config = DeployConfig()
    ssh = _build_ssh_from_config(config, "service", host, port, username, key, password)
    if not ssh.host or not ssh.username:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    service_name = name or Path(".").resolve().name
    if not ssh.connect():
        sys.exit(1)
    try:
        mgr = ServiceManager(ssh)
        status = mgr.get_status(service_name)
        if status:
            colour = "green" if status == "running" else "yellow"
            console.print(f"[{colour}]Service '{service_name}': {status}[/{colour}]")
        else:
            console.print(f"[yellow]Service '{service_name}' not found on remote[/yellow]")
    finally:
        ssh.disconnect()


# ---------------------------------------------------------------------------
# CLI root group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(__version__, prog_name="deploy")
def cli():
    """Git SSH Deploy Tool - Sync local Git repository to remote server over SSH."""
    pass


cli.add_command(main, name="push")
cli.add_command(pull, name="pull")
cli.add_command(show_config, name="show-config")
cli.add_command(clear_config, name="clear-config")
cli.add_command(caddy, name="caddy")
cli.add_command(docker_push, name="docker-push")
cli.add_command(proxy, name="proxy")
cli.add_command(service, name="service")


if __name__ == "__main__":
    cli()
