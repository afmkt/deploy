"""Caddy web server management module."""

import re
from pathlib import Path
from typing import Optional
from rich.console import Console
from .ssh import SSHConnection

console = Console()


class CaddyManager:
    """Manages Caddy web server configuration on remote servers."""

    def __init__(self, ssh: SSHConnection):
        """Initialize Caddy manager with SSH connection.

        Args:
            ssh: SSH connection to the remote server
        """
        self.ssh = ssh
        self._templates_dir = Path(__file__).parent / "templates"

    def is_caddy_installed(self) -> bool:
        """Check if Caddy is installed on the remote server.

        Returns:
            True if Caddy is installed, False otherwise
        """
        exit_code, stdout, stderr = self.ssh.execute("which caddy || command -v caddy")
        return exit_code == 0 and stdout.strip() != ""

    def get_caddy_version(self) -> Optional[str]:
        """Get the installed Caddy version.

        Returns:
            Caddy version string or None if not installed
        """
        exit_code, stdout, stderr = self.ssh.execute("caddy version 2>/dev/null || echo ''")
        if exit_code == 0 and stdout.strip():
            # Extract version from output like "v2.7.6 h1:wY..."
            match = re.search(r'v?(\d+\.\d+\.\d+)', stdout)
            if match:
                return match.group(1)
        return None

    def detect_os(self) -> Optional[str]:
        """Detect the operating system of the remote server.

        Returns:
            OS identifier string or None if detection fails
        """
        exit_code, stdout, stderr = self.ssh.execute("cat /etc/os-release 2>/dev/null || echo ''")
        if exit_code == 0 and stdout:
            if "ubuntu" in stdout.lower() or "debian" in stdout.lower():
                return "debian"
            elif "centos" in stdout.lower() or "rhel" in stdout.lower() or "fedora" in stdout.lower():
                return "rhel"
            elif "alpine" in stdout.lower():
                return "alpine"
        return None

    def install_caddy(self) -> bool:
        """Install Caddy on the remote server.

        Returns:
            True if installation successful, False otherwise
        """
        if self.is_caddy_installed():
            console.print("[yellow]Caddy is already installed[/yellow]")
            return True

        console.print("[blue]Installing Caddy...[/blue]")

        os_type = self.detect_os()
        if not os_type:
            console.print("[red]✗ Could not detect operating system[/red]")
            return False

        # Install based on OS type
        if os_type == "debian":
            commands = [
                "apt-get update",
                "apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl",
                "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg",
                "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list",
                "apt-get update",
                "apt-get install -y caddy",
            ]
        elif os_type == "rhel":
            commands = [
                "yum install -y yum-plugin-copr",
                "yum copr enable -y @caddy/caddy",
                "yum install -y caddy",
            ]
        elif os_type == "alpine":
            commands = [
                "apk update",
                "apk add caddy",
            ]
        else:
            console.print(f"[red]✗ Unsupported operating system: {os_type}[/red]")
            return False

        # Execute installation commands
        for cmd in commands:
            exit_code, stdout, stderr = self.ssh.execute(cmd)
            if exit_code != 0:
                console.print(f"[red]✗ Installation failed: {stderr}[/red]")
                return False

        # Verify installation
        if self.is_caddy_installed():
            version = self.get_caddy_version()
            console.print(f"[green]✓ Caddy installed successfully (version: {version})[/green]")
            return True
        else:
            console.print("[red]✗ Caddy installation verification failed[/red]")
            return False

    def get_caddy_config_path(self) -> str:
        """Get the Caddy configuration file path.

        Returns:
            Path to Caddyfile
        """
        # Check common locations
        common_paths = [
            "/etc/caddy/Caddyfile",
            "/usr/local/etc/caddy/Caddyfile",
            "/etc/caddy/caddy.conf",
        ]

        for path in common_paths:
            exit_code, stdout, stderr = self.ssh.execute(f"test -f {path} && echo 'exists'")
            if "exists" in stdout:
                return path

        # Default to standard location
        return "/etc/caddy/Caddyfile"

    def read_caddy_config(self) -> Optional[str]:
        """Read the current Caddy configuration.

        Returns:
            Caddyfile content or None if read fails
        """
        config_path = self.get_caddy_config_path()
        exit_code, stdout, stderr = self.ssh.execute(f"cat {config_path} 2>/dev/null || echo ''")

        if exit_code == 0:
            return stdout
        return None

    def parse_domains_and_ports(self, config: str) -> dict:
        """Parse Caddy config to extract domains and ports.

        Args:
            config: Caddyfile content

        Returns:
            Dictionary with 'domains' and 'ports' lists
        """
        domains = set()
        ports = set()

        # Match domain blocks: domain { ... }
        domain_pattern = r'^([a-zA-Z0-9][a-zA-Z0-9\-\.]*\.[a-zA-Z]{2,})\s*\{'
        # Match reverse_proxy with port: reverse_proxy localhost:PORT
        proxy_pattern = r'reverse_proxy\s+(?:localhost|127\.0\.0\.1):(\d+)'
        # Match standalone port listeners: :PORT
        port_pattern = r':(\d+)\s*\{'

        lines = config.split('\n')
        current_domain = None

        for line in lines:
            line = line.strip()

            # Check for domain block
            domain_match = re.match(domain_pattern, line)
            if domain_match:
                current_domain = domain_match.group(1)
                domains.add(current_domain)

            # Check for reverse_proxy port
            proxy_match = re.search(proxy_pattern, line)
            if proxy_match:
                ports.add(proxy_match.group(1))

            # Check for standalone port listener
            port_match = re.search(port_pattern, line)
            if port_match:
                ports.add(port_match.group(1))

        return {
            "domains": sorted(list(domains)),
            "ports": sorted(list(ports), key=int),
        }

    def parse_detailed_config(self, config: str) -> dict:
        """Parse Caddy config to extract detailed configurations.

        Args:
            config: Caddyfile content

        Returns:
            Dictionary with 'domains' and 'public_services' lists
        """
        domains_config = []
        public_services = []
        lines = config.split('\n')
        current_domain = None
        current_config = []
        brace_count = 0

        for line in lines:
            stripped = line.strip()
            
            # Check for domain block start
            domain_match = re.match(r'^([a-zA-Z0-9][a-zA-Z0-9\-\.]*\.[a-zA-Z]{2,})\s*\{', stripped)
            if domain_match:
                # Save previous domain config if exists
                if current_domain and current_config:
                    domains_config.append({
                        "domain": current_domain,
                        "config_lines": current_config,
                        "ports": self._extract_ports_from_config(current_config),
                        "paths": self._extract_paths_from_config(current_config),
                        "services": self._extract_services_from_config(current_config),
                        "public_port": self._extract_public_port_from_config(current_config),
                    })
                
                current_domain = domain_match.group(1)
                current_config = []
                brace_count = 1
                continue
            
            # Check for public IP/port block (e.g., :8080 { or 0.0.0.0:8080 { or localhost:8080 { or 127.0.0.1:8080 {)
            public_match = re.match(r'^(?::(\d+)|((?:0\.0\.0\.0|\*|localhost|127\.0\.0\.1)):(\d+))\s*\{', stripped)
            if public_match:
                # Save previous domain config if exists
                if current_domain and current_config:
                    domains_config.append({
                        "domain": current_domain,
                        "config_lines": current_config,
                        "ports": self._extract_ports_from_config(current_config),
                        "paths": self._extract_paths_from_config(current_config),
                        "services": self._extract_services_from_config(current_config),
                        "public_port": self._extract_public_port_from_config(current_config),
                    })
                
                # Start public service block
                listen_address = public_match.group(2) or ""
                public_port = public_match.group(1) or public_match.group(3)
                current_domain = f"public:{listen_address}:{public_port}" if listen_address else f"public::{public_port}"
                current_config = []
                brace_count = 1
                continue
            
            # Track brace depth
            if current_domain:
                brace_count += stripped.count('{')
                brace_count -= stripped.count('}')
                
                if brace_count > 0:
                    current_config.append(stripped)
                elif brace_count == 0:
                    # End of block
                    if current_config:
                        if current_domain.startswith("public:"):
                            # This is a public service
                            parts = current_domain.split(":")
                            listen_address = parts[1] if len(parts) > 2 else ""
                            listen_port = parts[-1]
                            public_services.append({
                                "listen_address": listen_address,
                                "listen_port": listen_port,
                                "config_lines": current_config,
                                "ports": self._extract_ports_from_config(current_config),
                                "paths": self._extract_paths_from_config(current_config),
                                "services": self._extract_services_from_config(current_config),
                            })
                        else:
                            # This is a domain
                            domains_config.append({
                                "domain": current_domain,
                                "config_lines": current_config,
                                "ports": self._extract_ports_from_config(current_config),
                                "paths": self._extract_paths_from_config(current_config),
                                "services": self._extract_services_from_config(current_config),
                                "public_port": self._extract_public_port_from_config(current_config),
                            })
                    current_domain = None
                    current_config = []

        # Handle last block if file doesn't end with closing brace
        if current_domain and current_config:
            if current_domain.startswith("public:"):
                parts = current_domain.split(":")
                listen_address = parts[1] if len(parts) > 2 else ""
                listen_port = parts[-1]
                public_services.append({
                    "listen_address": listen_address,
                    "listen_port": listen_port,
                    "config_lines": current_config,
                    "ports": self._extract_ports_from_config(current_config),
                    "paths": self._extract_paths_from_config(current_config),
                    "services": self._extract_services_from_config(current_config),
                })
            else:
                domains_config.append({
                    "domain": current_domain,
                    "config_lines": current_config,
                    "ports": self._extract_ports_from_config(current_config),
                    "paths": self._extract_paths_from_config(current_config),
                    "services": self._extract_services_from_config(current_config),
                    "public_port": self._extract_public_port_from_config(current_config),
                })

        return {
            "domains": domains_config,
            "public_services": public_services,
        }

    def _extract_ports_from_config(self, config_lines: list) -> list:
        """Extract port numbers from configuration lines.

        Args:
            config_lines: List of configuration lines

        Returns:
            List of port numbers
        """
        ports = set()
        proxy_pattern = r'reverse_proxy\s+(?:localhost|127\.0\.0\.1):(\d+)'
        
        for line in config_lines:
            match = re.search(proxy_pattern, line)
            if match:
                ports.add(match.group(1))
        
        return sorted(list(ports), key=int)

    def _extract_paths_from_config(self, config_lines: list) -> list:
        """Extract path patterns from configuration lines.

        Args:
            config_lines: List of configuration lines

        Returns:
            List of path patterns
        """
        paths = set()
        path_pattern = r'handle\s+([^\s{]+)'
        
        for line in config_lines:
            match = re.search(path_pattern, line)
            if match:
                paths.add(match.group(1))
        
        return sorted(list(paths))

    def _extract_services_from_config(self, config_lines: list) -> list:
        """Extract service information from configuration lines.

        Args:
            config_lines: List of configuration lines

        Returns:
            List of service descriptions
        """
        services = []
        
        for line in config_lines:
            # Check for reverse_proxy directives
            if 'reverse_proxy' in line:
                # Extract the target
                match = re.search(r'reverse_proxy\s+([^\s]+)', line)
                if match:
                    services.append(f"Proxy to {match.group(1)}")
            # Check for file_server directives
            elif 'file_server' in line:
                services.append("Static file server")
            # Check for respond directives
            elif 'respond' in line:
                match = re.search(r'respond\s+["\']([^"\']*)["\']', line)
                if match:
                    services.append(f"Respond with: {match.group(1)}")
            # Check for redir directives
            elif 'redir' in line:
                services.append("Redirect")
        
        return services

    def _extract_public_port_from_config(self, config_lines: list) -> Optional[str]:
        """Extract public port from configuration lines.

        Args:
            config_lines: List of configuration lines

        Returns:
            Public port string or None
        """
        # Look for tls directive which indicates HTTPS (port 443)
        for line in config_lines:
            if 'tls' in line:
                return "443"
        
        # Default to HTTP port 80 if no TLS
        return "80"

    def load_template(self, template_name: str = "default") -> Optional[str]:
        """Load a Caddy configuration template.

        Args:
            template_name: Name of the template (without .conf extension)

        Returns:
            Template content or None if not found
        """
        template_path = self._templates_dir / f"caddy_{template_name}.conf"

        if not template_path.exists():
            console.print(f"[red]✗ Template not found: {template_name}[/red]")
            return None

        try:
            with open(template_path, "r") as f:
                return f.read()
        except Exception as e:
            console.print(f"[red]✗ Failed to load template: {e}[/red]")
            return None

    def render_template(self, template: str, domain: str, port: int) -> str:
        """Render a template with domain and port values.

        Args:
            template: Template content
            domain: Domain name
            port: Port number

        Returns:
            Rendered configuration
        """
        return template.replace("{domain}", domain).replace("{port}", str(port))

    def entry_exists(self, domain: str) -> bool:
        """Check if a domain entry already exists in the Caddyfile.

        Args:
            domain: Domain name to check

        Returns:
            True if entry exists, False otherwise
        """
        config = self.read_caddy_config()
        if not config:
            return False

        # Check if domain appears in the config
        pattern = rf'^{re.escape(domain)}\s*\{{'
        return bool(re.search(pattern, config, re.MULTILINE))

    def add_entry(self, domain: str, port: int, template_name: str = "default") -> bool:
        """Add a new entry to the Caddy configuration.

        Args:
            domain: Domain name
            port: Port number
            template_name: Name of the template to use

        Returns:
            True if entry added successfully, False otherwise
        """
        # Check if entry already exists
        if self.entry_exists(domain):
            console.print(f"[yellow]Entry for {domain} already exists[/yellow]")
            return False

        # Load template
        template = self.load_template(template_name)
        if not template:
            return False

        # Render template
        entry = self.render_template(template, domain, port)

        # Get config path
        config_path = self.get_caddy_config_path()

        # Append entry to Caddyfile
        console.print(f"[blue]Adding entry for {domain}:{port}...[/blue]")

        # Create backup first
        backup_cmd = f"cp {config_path} {config_path}.backup.$(date +%Y%m%d%H%M%S)"
        self.ssh.execute(backup_cmd)

        # Append the new entry
        # Use printf to avoid shell escaping issues
        escaped_entry = entry.replace("'", "'\\''")
        append_cmd = f"printf '\\n{escaped_entry}\\n' >> {config_path}"
        exit_code, stdout, stderr = self.ssh.execute(append_cmd)

        if exit_code != 0:
            console.print(f"[red]✗ Failed to add entry: {stderr}[/red]")
            return False

        console.print(f"[green]✓ Added entry for {domain}:{port}[/green]")
        return True

    def reload_caddy(self) -> bool:
        """Reload Caddy configuration.

        Returns:
            True if reload successful, False otherwise
        """
        console.print("[blue]Reloading Caddy...[/blue]")

        # Try systemctl first
        exit_code, stdout, stderr = self.ssh.execute("systemctl reload caddy 2>/dev/null")
        if exit_code == 0:
            console.print("[green]✓ Caddy reloaded successfully[/green]")
            return True

        # Try caddy reload command
        config_path = self.get_caddy_config_path()
        exit_code, stdout, stderr = self.ssh.execute(f"caddy reload --config {config_path} 2>/dev/null")
        if exit_code == 0:
            console.print("[green]✓ Caddy reloaded successfully[/green]")
            return True

        # Try service command
        exit_code, stdout, stderr = self.ssh.execute("service caddy reload 2>/dev/null")
        if exit_code == 0:
            console.print("[green]✓ Caddy reloaded successfully[/green]")
            return True

        console.print("[yellow]⚠ Could not reload Caddy automatically. Please reload manually.[/yellow]")
        return False

    def validate_config(self) -> bool:
        """Validate the Caddy configuration.

        Returns:
            True if configuration is valid, False otherwise
        """
        config_path = self.get_caddy_config_path()
        exit_code, stdout, stderr = self.ssh.execute(f"caddy validate --config {config_path} 2>/dev/null")

        if exit_code == 0:
            console.print("[green]✓ Caddy configuration is valid[/green]")
            return True
        else:
            console.print(f"[red]✗ Caddy configuration is invalid: {stderr}[/red]")
            return False

    def get_available_templates(self) -> list:
        """Get list of available template names.

        Returns:
            List of template names
        """
        templates = []
        if self._templates_dir.exists():
            for file in self._templates_dir.glob("caddy_*.conf"):
                # Extract template name from filename
                name = file.stem.replace("caddy_", "")
                templates.append(name)
        return sorted(templates)
