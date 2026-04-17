"""Service init workflow argument resolution and execution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .ingress import normalize_ingress_networks
from .service import (
    detect_fastapi_entrypoint,
    render_dockerfile,
    render_service_compose,
    write_service_skill,
)


@dataclass(slots=True)
class ServiceInitExecutionContext:
    """Fully resolved arguments required to execute deploy service init."""

    project_dir: Path
    service_name: str
    domain: str | None
    port: int
    image: str | None
    ingress_networks: tuple[str, ...]
    global_ingress: bool
    path_prefix: str | None
    internal: bool
    force: bool
    entrypoint_file: str
    app_str: str
    resolved_arguments: tuple["ResolvedArgument", ...]


@dataclass(slots=True)
class ResolvedArgument:
    """A resolved argument value and where it originated from."""

    name: str
    value: str
    origin: str


@dataclass(slots=True)
class ServiceInitResolutionResult:
    """Resolved service-init execution context."""

    context: ServiceInitExecutionContext


class ServiceInitArgumentResolver:
    """Resolve service-init arguments from CLI input and local defaults."""

    def resolve(
        self,
        *,
        domain: str | None,
        name: str | None,
        port: int | None,
        image: str | None,
        ingress_networks: tuple[str, ...],
        global_ingress: bool,
        path_prefix: str | None,
        force: bool,
    ) -> ServiceInitResolutionResult | None:
        project_dir = Path(".")
        service_name = name or project_dir.resolve().name

        internal = not bool(domain)

        if not image:
            return None

        resolved_domain = domain

        entrypoint_file, app_str, default_port = detect_fastapi_entrypoint(project_dir)
        effective_port = port or default_port
        resolved_ingress_networks = tuple(normalize_ingress_networks(ingress_networks))

        resolved_arguments = (
            ResolvedArgument(
                name="name",
                value=service_name,
                origin="cli (--name)" if name else "default (current directory name)",
            ),
            *([ResolvedArgument(
                name="domain",
                value=resolved_domain,
                origin="cli (--domain)",
            )] if resolved_domain else []),
            ResolvedArgument(
                name="port",
                value=str(effective_port),
                origin="cli (--port)" if port is not None else f"detected from {entrypoint_file}",
            ),
            ResolvedArgument(
                name="image",
                value=image,
                origin="cli/config/prompt (--image)",
            ),
            ResolvedArgument(
                name="ingress_networks",
                value=", ".join(resolved_ingress_networks),
                origin="cli (--network)" if ingress_networks else "default (ingress)",
            ),
            ResolvedArgument(
                name="global_ingress",
                value=str(global_ingress).lower(),
                origin="cli (--global)" if global_ingress else "default (--no-global)",
            ),
            ResolvedArgument(
                name="path_prefix",
                value=path_prefix or "<none>",
                origin="cli (--path-prefix)" if path_prefix else "default (none)",
            ),
            ResolvedArgument(
                name="internal",
                value=str(internal).lower(),
                origin="derived (no --domain provided)" if internal else "derived (--domain provided)",
            ),
            ResolvedArgument(
                name="force",
                value=str(force).lower(),
                origin="cli (--force)" if force else "default (false)",
            ),
            ResolvedArgument(
                name="entrypoint",
                value=f"{entrypoint_file} -> {app_str}",
                origin="detected from project files",
            ),
        )

        return ServiceInitResolutionResult(
            context=ServiceInitExecutionContext(
                project_dir=project_dir,
                service_name=service_name,
                domain=resolved_domain,
                port=effective_port,
                image=image,
                ingress_networks=resolved_ingress_networks,
                global_ingress=global_ingress,
                path_prefix=path_prefix,
                internal=internal,
                force=force,
                entrypoint_file=entrypoint_file,
                app_str=app_str,
                resolved_arguments=resolved_arguments,
            )
        )


def execute_service_init(context: ServiceInitExecutionContext, console: Console) -> bool:
    """Execute deploy service init using fully resolved arguments."""
    domain_display = f"Domain: {context.domain}  " if context.domain else ""
    console.print(Panel.fit(
        f"[bold blue]Service init - {context.service_name}[/bold blue]\n"
        f"{domain_display}Port: {context.port}"
        + (f"  Path: {context.path_prefix}" if context.path_prefix else "")
        + ("  [internal]" if context.internal else ""),
        border_style="blue",
    ))
    console.print(f"[dim]Detected entrypoint: {context.entrypoint_file} -> {context.app_str}[/dim]")
    artifacts: list[tuple[str, Path, str]] = []

    dockerfile_path = context.project_dir / "Dockerfile"
    dockerfile_existed = dockerfile_path.exists()
    if dockerfile_path.exists() and not context.force:
        console.print("[dim]Dockerfile already exists, skipping (use --force to overwrite)[/dim]")
        artifacts.append(("Dockerfile", dockerfile_path, "skipped"))
    else:
        dockerfile_path.write_text(render_dockerfile(context.app_str, context.port))
        console.print(f"[green]✓ Wrote {dockerfile_path}[/green]")
        artifacts.append(("Dockerfile", dockerfile_path, "overwritten" if dockerfile_existed else "created"))

    compose_path = context.project_dir / "docker-compose.yml"
    compose_existed = compose_path.exists()
    if compose_path.exists() and not context.force:
        console.print("[dim]docker-compose.yml already exists, skipping (use --force to overwrite)[/dim]")
        artifacts.append(("docker-compose.yml", compose_path, "skipped"))
    else:
        compose_content = render_service_compose(
            service_name=context.service_name,
            domain=context.domain,
            port=context.port,
            image=context.image,
            ingress_networks=context.ingress_networks,
            exposure_scope="global" if context.global_ingress else "single",
            path_prefix=context.path_prefix,
            internal=context.internal,
        )
        compose_path.write_text(compose_content)
        console.print(f"[green]✓ Wrote {compose_path}[/green]")
        artifacts.append(("docker-compose.yml", compose_path, "overwritten" if compose_existed else "created"))

    skill_file_path = context.project_dir / ".github/skills/deploy-service/SKILL.md"
    skill_existed = skill_file_path.exists()

    skill_path = write_service_skill(
        project_dir=context.project_dir,
        service_name=context.service_name,
        domain=context.domain,
        port=context.port,
        image=context.image,
        ingress_networks=context.ingress_networks,
        exposure_scope="global" if context.global_ingress else "single",
        path_prefix=context.path_prefix,
        internal=context.internal,
        force=context.force,
    )
    if skill_path is None:
        console.print("[dim]Service skill already exists, skipping (use --force to overwrite)[/dim]")
        artifacts.append(("service skill", skill_file_path, "skipped"))
    else:
        console.print(f"[green]✓ Wrote {skill_path}[/green]")
        artifacts.append(("service skill", skill_path, "overwritten" if skill_existed else "created"))

    console.print("\n[bold green]✓ Service initialised[/bold green]")
    console.print("\n[bold]Summary[/bold]")
    console.print("[bold]1. Created or updated artifacts[/bold]")
    for label, path, status in artifacts:
        console.print(f"  - {label}: {status} ({path})")

    console.print("\n[bold]2. Resolved arguments (value <- origin)[/bold]")
    for argument in context.resolved_arguments:
        console.print(f"  - {argument.name}: {argument.value} [dim]<- {argument.origin}[/dim]")

    console.print("\n[bold]3. Most likely customization points[/bold]")
    console.print("  - Dockerfile: runtime base image, dependency install strategy, startup command")
    console.print("  - docker-compose.yml: image/build mode, labels/routing, env vars, mounts, restart policy")
    console.print("  - Flags to rerun with: --path-prefix, --network, --global, --force")

    suggested_image = context.image or f"{context.service_name}:latest"
    next_command = (
        f"deploy image build --tag {suggested_image} --remote <host> --username <username>\n"
        f"  deploy svc up -n {context.service_name} --remote <host> --username <username>"
    )
    console.print("\n[bold]4. Most likely next command[/bold]")
    console.print(f"  [dim]{next_command}[/dim]")
    return True
