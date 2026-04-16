"""Service init workflow argument resolution and execution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .service import (
    detect_fastapi_entrypoint,
    render_dockerfile,
    render_service_compose,
    render_service_metadata,
    write_service_skill,
)


@dataclass(slots=True)
class ServiceInitExecutionContext:
    """Fully resolved arguments required to execute deploy service init."""

    project_dir: Path
    service_name: str
    domain: str
    port: int
    image: str | None
    ingress_networks: tuple[str, ...]
    global_ingress: bool
    path_prefix: str | None
    internal: bool
    force: bool
    entrypoint_file: str
    app_str: str


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
        internal: bool,
        force: bool,
    ) -> ServiceInitResolutionResult | None:
        project_dir = Path(".")
        service_name = name or project_dir.resolve().name

        if not domain and not internal:
            return None

        resolved_domain = domain or service_name

        entrypoint_file, app_str, default_port = detect_fastapi_entrypoint(project_dir)
        effective_port = port or default_port

        return ServiceInitResolutionResult(
            context=ServiceInitExecutionContext(
                project_dir=project_dir,
                service_name=service_name,
                domain=resolved_domain,
                port=effective_port,
                image=image,
                ingress_networks=tuple(ingress_networks),
                global_ingress=global_ingress,
                path_prefix=path_prefix,
                internal=internal,
                force=force,
                entrypoint_file=entrypoint_file,
                app_str=app_str,
            )
        )


def execute_service_init(context: ServiceInitExecutionContext, console: Console) -> bool:
    """Execute deploy service init using fully resolved arguments."""
    console.print(Panel.fit(
        f"[bold blue]Service init - {context.service_name}[/bold blue]\n"
        f"Domain: {context.domain}  Port: {context.port}"
        + (f"  Path: {context.path_prefix}" if context.path_prefix else "")
        + ("  [internal]" if context.internal else ""),
        border_style="blue",
    ))
    console.print(f"[dim]Detected entrypoint: {context.entrypoint_file} -> {context.app_str}[/dim]")

    dockerfile_path = context.project_dir / "Dockerfile"
    if dockerfile_path.exists() and not context.force:
        console.print("[dim]Dockerfile already exists, skipping (use --force to overwrite)[/dim]")
    else:
        dockerfile_path.write_text(render_dockerfile(context.app_str, context.port))
        console.print(f"[green]✓ Wrote {dockerfile_path}[/green]")

    compose_path = context.project_dir / "docker-compose.yml"
    if compose_path.exists() and not context.force:
        console.print("[dim]docker-compose.yml already exists, skipping (use --force to overwrite)[/dim]")
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

    metadata_path = context.project_dir / ".deploy-service.json"
    metadata_path.write_text(
        render_service_metadata(
            service_name=context.service_name,
            domain=context.domain,
            port=context.port,
            image=context.image,
            ingress_networks=context.ingress_networks,
            exposure_scope="global" if context.global_ingress else "single",
            path_prefix=context.path_prefix,
            internal=context.internal,
        )
    )
    console.print(f"[green]✓ Wrote {metadata_path}[/green]")

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
    else:
        console.print(f"[green]✓ Wrote {skill_path}[/green]")

    console.print("\n[bold green]✓ Service initialised[/bold green]")
    console.print("  Next: [dim]deploy service deploy --host <host> --image <image>[/dim]")
    return True
