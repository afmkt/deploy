"""Git SSH Deploy Tool - Main CLI entry point."""

import sys
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from deploy.git import GitRepository
from deploy.ssh import SSHConnection
from deploy.remote import RemoteServer
from deploy.caddy import CaddyManager
from deploy.config import DeployConfig
from deploy.utils import (
    prompt_connection_details,
    prompt_deploy_path,
    print_summary,
)

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

        # Print summary
        print_summary(host, repo_name, bare_repo_url, working_dir_path)

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

        # Check if bare repository exists
        if not remote.directory_exists(bare_repo_path):
            console.print(f"[red]✗ Remote repository does not exist: {bare_repo_path}[/red]")
            sys.exit(1)

        # Optional: Sync remote working directory (check clean, commit, push, then pull)
        if sync_remote:
            console.print("\n[bold]Step 4: Checking if remote working directory is clean[/bold]")
            # Check if there are uncommitted changes
            exit_code, stdout, stderr = ssh.execute(
                f"cd {working_dir_path} && git status --porcelain"
            )
            if exit_code != 0:
                console.print(f"[red]✗ Failed to check git status: {stderr}[/red]")
                sys.exit(1)
            
            has_uncommitted = bool(stdout.strip())
            
            # Check if there are unpushed commits
            exit_code, stdout, stderr = ssh.execute(
                f"cd {working_dir_path} && git log origin/$(git rev-parse --abbrev-ref HEAD)..HEAD --oneline 2>/dev/null || echo ''"
            )
            has_unpushed = bool(stdout.strip()) if exit_code == 0 else False
            
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

        console.print("\n[green]✓ Pull operation completed successfully[/green]")

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
def caddy(host: str, port: int, username: str, key: str, password: str,
          template: str, interactive: bool, use_config: bool, dry_run: bool):
    """Setup and configure Caddy reverse proxy on remote server.
    
    This command helps you install Caddy, analyze existing configuration,
    and add new reverse proxy entries.
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

        # Get user input for new entry
        console.print("\n[bold]Step 5: Configure new entry[/bold]")
        
        # Show available templates
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
        console.print("\n[bold]Step 6: Adding Caddy entry[/bold]")
        if not caddy_mgr.add_entry(domain, port_num, template):
            console.print("[red]✗ Failed to add entry[/red]")
            sys.exit(1)

        # Validate configuration
        console.print("\n[bold]Step 7: Validating configuration[/bold]")
        if not caddy_mgr.validate_config():
            console.print("[red]✗ Configuration validation failed[/red]")
            sys.exit(1)

        # Reload Caddy
        console.print("\n[bold]Step 8: Reloading Caddy[/bold]")
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


@click.group()
def cli():
    """Git SSH Deploy Tool - Sync local Git repository to remote server over SSH."""
    pass


cli.add_command(main, name="push")
cli.add_command(pull, name="pull")
cli.add_command(show_config, name="show-config")
cli.add_command(clear_config, name="clear-config")
cli.add_command(caddy, name="caddy")


if __name__ == "__main__":
    cli()
