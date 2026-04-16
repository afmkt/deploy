"""Docker image transfer module."""

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console

from .local import LocalConnection
from .ssh import SSHConnection

console = Console()


def _safe_image_filename(image: str) -> str:
    """Convert an image reference to a safe tarball filename."""
    return image.replace(":", "_").replace("/", "_") + ".tar"


class DockerManager:
    """Manages Docker installation checks and image transfer to remote servers."""

    def __init__(self, ssh: SSHConnection):
        self.ssh = ssh

    @property
    def is_local(self) -> bool:
        return bool(getattr(self.ssh, "is_local", False))

    @staticmethod
    def _q(value: str) -> str:
        """Shell-quote a value for safe use in remote commands."""
        return shlex.quote(value)

    # ------------------------------------------------------------------
    # Remote: Docker installation
    # ------------------------------------------------------------------

    def is_docker_installed(self) -> bool:
        """Check whether Docker (daemon) is available on the remote server."""
        exit_code, stdout, _ = self.ssh.execute(
            "docker version --format '{{.Server.Version}}' 2>/dev/null && echo OK"
        )
        return exit_code == 0 and "OK" in stdout

    def get_docker_version(self) -> Optional[str]:
        """Return the remote Docker server version string, or None."""
        exit_code, stdout, _ = self.ssh.execute(
            "docker version --format '{{.Server.Version}}' 2>/dev/null"
        )
        if exit_code == 0 and stdout.strip():
            return stdout.strip()
        return None

    def detect_os(self) -> Optional[str]:
        """Detect the remote server OS family."""
        exit_code, stdout, _ = self.ssh.execute("cat /etc/os-release 2>/dev/null || echo ''")
        if exit_code == 0 and stdout:
            lower = stdout.lower()
            if "ubuntu" in lower or "debian" in lower:
                return "debian"
            if "centos" in lower or "rhel" in lower or "fedora" in lower or "amazon" in lower:
                return "rhel"
            if "alpine" in lower:
                return "alpine"
        return None

    def install_docker(self) -> bool:
        """Install Docker on the remote server (requires root / sudo)."""
        if self.is_docker_installed():
            console.print("[yellow]Docker is already installed[/yellow]")
            return True

        console.print("[blue]Installing Docker...[/blue]")
        os_type = self.detect_os()
        if not os_type:
            console.print("[red]✗ Could not detect remote OS; cannot install Docker[/red]")
            return False

        if os_type == "debian":
            commands = [
                "apt-get update -qq",
                "apt-get install -y ca-certificates curl",
                "install -m 0755 -d /etc/apt/keyrings",
                "curl -fsSL https://download.docker.com/linux/ubuntu/gpg"
                " -o /etc/apt/keyrings/docker.asc",
                "chmod a+r /etc/apt/keyrings/docker.asc",
                'echo "deb [arch=$(dpkg --print-architecture)'
                " signed-by=/etc/apt/keyrings/docker.asc]"
                " https://download.docker.com/linux/ubuntu"
                ' $(. /etc/os-release && echo "$VERSION_CODENAME") stable"'
                " | tee /etc/apt/sources.list.d/docker.list > /dev/null",
                "apt-get update -qq",
                "apt-get install -y docker-ce docker-ce-cli containerd.io",
                "systemctl enable docker --now 2>/dev/null || service docker start || true",
            ]
        elif os_type == "rhel":
            commands = [
                "yum install -y yum-utils",
                "yum-config-manager --add-repo"
                " https://download.docker.com/linux/centos/docker-ce.repo",
                "yum install -y docker-ce docker-ce-cli containerd.io",
                "systemctl enable docker --now 2>/dev/null || service docker start || true",
            ]
        elif os_type == "alpine":
            commands = [
                "apk update",
                "apk add docker",
                "rc-update add docker boot 2>/dev/null || true",
                "service docker start 2>/dev/null || true",
            ]
        else:
            console.print(f"[red]✗ Unsupported OS type: {os_type}[/red]")
            return False

        for cmd in commands:
            exit_code, _, stderr = self.ssh.execute(cmd)
            if exit_code != 0:
                console.print(f"[red]✗ Installation step failed: {stderr}[/red]")
                return False

        if self.is_docker_installed():
            version = self.get_docker_version()
            console.print(f"[green]✓ Docker installed successfully (version: {version})[/green]")
            return True

        console.print("[red]✗ Docker installation verification failed[/red]")
        return False

    # ------------------------------------------------------------------
    # Remote: architecture detection
    # ------------------------------------------------------------------

    def detect_remote_arch(self) -> Optional[str]:
        """Return a Docker platform string matching the remote server architecture."""
        exit_code, stdout, _ = self.ssh.execute("uname -m")
        if exit_code != 0:
            console.print("[red]✗ Could not detect remote architecture[/red]")
            return None
        arch = stdout.strip()
        arch_map: dict[str, str] = {
            "x86_64": "linux/amd64",
            "aarch64": "linux/arm64",
            "arm64": "linux/arm64",
            "armv7l": "linux/arm/v7",
            "armv6l": "linux/arm/v6",
            "i386": "linux/386",
            "i686": "linux/386",
            "s390x": "linux/s390x",
            "ppc64le": "linux/ppc64le",
        }
        platform = arch_map.get(arch)
        if not platform:
            console.print(
                f"[yellow]Unknown remote arch '{arch}', defaulting to linux/amd64[/yellow]"
            )
            return "linux/amd64"
        console.print(f"[green]✓ Remote architecture: {arch} → {platform}[/green]")
        return platform

    # ------------------------------------------------------------------
    # Local: pull and save
    # ------------------------------------------------------------------

    def registry_login(self, registry_username: str, registry_password: str, image: str) -> bool:
        """Log in to a Docker registry using password via stdin (avoids shell history)."""
        first_component = image.split("/")[0]
        registry = first_component if ("." in first_component or ":" in first_component) else ""
        login_cmd = ["docker", "login", "--username", registry_username, "--password-stdin"]
        if registry:
            login_cmd.append(registry)
        console.print("[blue]Logging into Docker registry...[/blue]")
        try:
            result = subprocess.run(
                login_cmd,
                input=registry_password,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                console.print(f"[red]✗ Registry login failed: {result.stderr.strip()}[/red]")
                return False
            console.print("[green]✓ Logged into registry[/green]")
            return True
        except FileNotFoundError:
            console.print("[red]✗ Docker not found locally[/red]")
            return False

    def pull_image(self, image: str, platform: str) -> bool:
        """Pull a Docker image locally for the specified platform."""
        console.print(f"[blue]Pulling {image} for platform {platform}...[/blue]")
        try:
            result = subprocess.run(
                ["docker", "pull", "--platform", platform, image],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                console.print(f"[red]✗ docker pull failed: {result.stderr.strip()}[/red]")
                return False
            console.print(f"[green]✓ Pulled {image}[/green]")
            return True
        except FileNotFoundError:
            console.print("[red]✗ Docker not found locally; is Docker installed and in PATH?[/red]")
            return False

    def get_local_image_id(self, image: str) -> Optional[str]:
        """Return the local image ID (sha256 digest) for a given image reference."""
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.Id}}", image],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except FileNotFoundError:
            return None

    def save_image(self, image: str, local_tar_path: str, platform: Optional[str] = None) -> bool:
        """Save a local Docker image to a tarball.

        Prefer saving with an explicit platform to avoid a containerd image-store
        bug where docker save tries to export the full multi-arch manifest list
        even though only one platform's layers were pulled locally.

        If the local Docker version does not support docker save --platform,
        fall back to saving by image ID. The original name:tag is restored on
        the remote via load_image() when necessary.
        """
        console.print(f"[blue]Saving image to {local_tar_path}...[/blue]")

        save_commands: list[list[str]] = []
        if platform:
            save_commands.append(["docker", "save", "--platform", platform, image, "-o", local_tar_path])

        # Fallback for older Docker versions without --platform support.
        image_id = self.get_local_image_id(image)
        save_ref = image_id if image_id else image
        fallback_command = ["docker", "save", save_ref, "-o", local_tar_path]
        if not platform or fallback_command not in save_commands:
            save_commands.append(fallback_command)

        try:
            last_error = ""
            for index, command in enumerate(save_commands):
                result = subprocess.run(command, capture_output=True, text=True)
                if result.returncode == 0:
                    size_mb = Path(local_tar_path).stat().st_size / (1024 * 1024)
                    console.print(f"[green]✓ Saved image ({size_mb:.1f} MB)[/green]")
                    return True

                last_error = result.stderr.strip()
                if platform and index == 0:
                    console.print(
                        "[yellow]docker save --platform failed; retrying with local image ID[/yellow]"
                    )

            console.print(f"[red]✗ docker save failed: {last_error}[/red]")
            return False
        except FileNotFoundError:
            console.print("[red]✗ Docker not found locally[/red]")
            return False

    # ------------------------------------------------------------------
    # Transfer via SFTP
    # ------------------------------------------------------------------

    def transfer_tarball(self, local_path: str, remote_path: str) -> bool:
        """Upload a local tarball to the remote server via SFTP."""
        if self.is_local:
            try:
                size_mb = Path(local_path).stat().st_size / (1024 * 1024)
                console.print(f"[blue]Copying {size_mb:.1f} MB locally → {remote_path}...[/blue]")
                LocalConnection.copy_file(local_path, remote_path)
                console.print("[green]✓ Local copy complete[/green]")
                return True
            except Exception as e:
                console.print(f"[red]✗ Local copy failed: {e}[/red]")
                return False

        try:
            size_mb = Path(local_path).stat().st_size / (1024 * 1024)
            console.print(f"[blue]Transferring {size_mb:.1f} MB → {remote_path}...[/blue]")
            assert self.ssh.client is not None
            sftp = self.ssh.client.open_sftp()
            sftp.put(local_path, remote_path)
            sftp.close()
            console.print(f"[green]✓ Transfer complete[/green]")
            return True
        except Exception as e:
            console.print(f"[red]✗ Transfer failed: {e}[/red]")
            return False

    # ------------------------------------------------------------------
    # Remote: load and cleanup
    # ------------------------------------------------------------------

    def load_image(self, remote_tar_path: str, image_tag: Optional[str] = None) -> bool:
        """Load a Docker image from a tarball on the remote server.

        When the tarball was saved by image ID (to work around the containerd
        manifest list bug), docker load produces "Loaded image ID: sha256:..."
        and the image has no name.  If image_tag is supplied, the loaded image
        is tagged with the original name:tag automatically.
        """
        console.print("[blue]Loading image on remote server...[/blue]")
        exit_code, stdout, stderr = self.ssh.execute(
            f"docker load -i {self._q(remote_tar_path)}"
        )
        if exit_code != 0:
            console.print(f"[red]✗ docker load failed: {stderr.strip()}[/red]")
            return False

        loaded = stdout.strip()
        console.print(f"[green]✓ Image loaded: {loaded}[/green]")

        # When saved by ID the output is "Loaded image ID: sha256:..."
        # Re-tag the image so it is addressable by the original name.
        if image_tag and "Loaded image ID:" in loaded:
            loaded_id = loaded.split("Loaded image ID:")[-1].strip()
            console.print(f"[blue]Tagging {loaded_id[:19]}... as {image_tag}[/blue]")
            tag_exit, _, tag_err = self.ssh.execute(
                f"docker tag {self._q(loaded_id)} {self._q(image_tag)}"
            )
            if tag_exit != 0:
                console.print(f"[yellow]⚠ Could not tag image: {tag_err.strip()}[/yellow]")
            else:
                console.print(f"[green]✓ Tagged as {image_tag}[/green]")

        return True

    def cleanup_remote(self, remote_tar_path: str) -> None:
        """Remove the tarball from the remote server."""
        if self.is_local:
            try:
                Path(remote_tar_path).unlink(missing_ok=True)
            except Exception:
                pass
            return
        self.ssh.execute(f"rm -f {self._q(remote_tar_path)}")
        console.print(f"[dim]Cleaned up remote tarball: {remote_tar_path}[/dim]")
