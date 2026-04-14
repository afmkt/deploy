"""Textual application for monitoring and controlling remote deployment state."""

from __future__ import annotations

import asyncio
from datetime import timezone

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, RichLog, Static, TabbedContent, TabPane

from .actions import ActionRunner
from .collector import MonitorCollector
from .models import ActionResult, Snapshot


class NetworkNameScreen(ModalScreen[str | None]):
    """Modal prompt for creating a network."""

    def compose(self) -> ComposeResult:
        with Vertical(id="network-modal"):
            yield Label("Create Docker Network", id="network-title")
            yield Input(placeholder="network name", id="network-name")
            with Horizontal(id="network-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Create", id="create", variant="primary")

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#create")
    def create(self) -> None:
        value = self.query_one("#network-name", Input).value.strip()
        self.dismiss(value or None)


class ConfirmScreen(ModalScreen[bool]):
    """Simple yes/no confirmation dialog for destructive actions."""

    def __init__(self, title: str, message: str):
        super().__init__()
        self._title = title
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="network-modal"):
            yield Label(self._title, id="network-title")
            yield Static(self._message)
            with Horizontal(id="network-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Confirm", id="confirm", variant="error")

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#confirm")
    def confirm(self) -> None:
        self.dismiss(True)


class MonitorApp(App):
    """Long-running monitor TUI."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #top-status {
        height: auto;
        padding: 0 1;
    }

    #overview-pane, #resources-pane {
        padding: 1;
    }

    #logs-view {
        height: 1fr;
        border: round #666666;
    }

    #network-modal {
        width: 60;
        height: 12;
        border: round $accent;
        padding: 1;
        background: $panel;
    }

    #network-actions {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #network-title {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("u", "proxy_up", "Proxy Up"),
        Binding("d", "proxy_down", "Proxy Down"),
        Binding("s", "service_up", "Service Up"),
        Binding("x", "service_down", "Service Down"),
        Binding("t", "service_restart", "Service Restart"),
        Binding("n", "network_create", "Create Network"),
        Binding("l", "logs", "Fetch Logs"),
        Binding("c", "cancel_action", "Cancel Action"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        key_filename: str | None,
        password: str | None,
        refresh_interval: int = 5,
        log_lines: int = 120,
        command_timeout: float = 10.0,
        action_timeout: float = 15.0,
    ):
        super().__init__()
        self.refresh_interval = max(2, refresh_interval)
        self.log_lines = max(20, log_lines)
        self.command_timeout = max(1.0, float(command_timeout))
        self.action_timeout = max(self.command_timeout + 1.0, float(action_timeout))
        self.collector = MonitorCollector(
            host=host,
            port=port,
            username=username,
            key_filename=key_filename,
            password=password,
            command_timeout=self.command_timeout,
        )
        self.runner = ActionRunner(
            host=host,
            port=port,
            username=username,
            key_filename=key_filename,
            password=password,
            command_timeout=self.command_timeout,
        )
        self.snapshot = Snapshot()
        self.selected_service = ""
        self.action_in_progress = False
        self._action_seq = 0
        self._cancel_action_seq = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Connecting...", id="top-status")
        with TabbedContent(id="tabs"):
            with TabPane("Overview", id="tab-overview"):
                yield Static(id="overview-pane")
            with TabPane("Services", id="tab-services"):
                yield DataTable(id="services-table")
            with TabPane("Networks", id="tab-networks"):
                yield DataTable(id="networks-table")
            with TabPane("Resources", id="tab-resources"):
                yield Static(id="resources-pane")
            with TabPane("Logs", id="tab-logs"):
                yield RichLog(id="logs-view", highlight=False, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        services = self.query_one("#services-table", DataTable)
        services.add_columns("Service", "Status")
        services.cursor_type = "row"

        networks = self.query_one("#networks-table", DataTable)
        networks.add_columns("Network")
        networks.cursor_type = "row"

        self.set_interval(self.refresh_interval, self._schedule_refresh)
        self._schedule_refresh()

    @on(DataTable.RowHighlighted, "#services-table")
    def on_service_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is not None and event.row_key.value is not None:
            self.selected_service = str(event.row_key.value)

    def _schedule_refresh(self) -> None:
        self.run_worker(self._collect_and_apply(), group="refresh", exclusive=True)

    async def _collect_and_apply(self) -> None:
        snapshot = await asyncio.to_thread(self.collector.collect)
        self.snapshot = snapshot
        self._render_snapshot()

    def _render_snapshot(self) -> None:
        status = self.query_one("#top-status", Static)
        time_text = self.snapshot.timestamp.astimezone(timezone.utc).strftime("%H:%M:%SZ")
        if self.snapshot.connected:
            status.update(
                f"Host connected | Last refresh: {time_text} | Proxy: {self.snapshot.proxy_status} | "
                f"Services: {len(self.snapshot.services)} | Networks: {len(self.snapshot.networks)}"
            )
        else:
            status.update(f"Disconnected | Last refresh: {time_text} | Error: {self.snapshot.error or 'n/a'}")

        self.query_one("#overview-pane", Static).update(
            "\n".join([
                f"Proxy status: {self.snapshot.proxy_status}",
                f"Services discovered: {len(self.snapshot.services)}",
                f"Networks discovered: {len(self.snapshot.networks)}",
                f"Host load avg: {self.snapshot.resources.load_avg}",
            ])
        )

        services_table = self.query_one("#services-table", DataTable)
        services_table.clear(columns=False)
        for svc in self.snapshot.services:
            services_table.add_row(svc.name, svc.status, key=svc.name)

        if not self.selected_service and self.snapshot.services:
            self.selected_service = self.snapshot.services[0].name

        networks_table = self.query_one("#networks-table", DataTable)
        networks_table.clear(columns=False)
        for name in self.snapshot.networks:
            networks_table.add_row(name, key=name)

        resources = self.snapshot.resources
        self.query_one("#resources-pane", Static).update(
            "\n".join([
                f"Load avg: {resources.load_avg}",
                f"Memory: {resources.memory}",
                f"Disk (/): {resources.disk}",
                f"Docker containers: {resources.docker_containers}",
                f"Docker images: {resources.docker_images}",
            ])
        )

    async def _run_action(self, action: str, target: str = "", value: str = "") -> None:
        if self.action_in_progress:
            self.notify("Another action is still running", severity="warning")
            return

        self.action_in_progress = True
        self._action_seq += 1
        action_seq = self._action_seq
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self.runner.run, action, target, value),
                timeout=self.action_timeout,
            )
            if self._cancel_action_seq == action_seq:
                self._record_action(
                    ActionResult(
                        ok=False,
                        action=action,
                        message="Action cancelled by operator (remote command may still complete)",
                    )
                )
                return
            self._record_action(result)
            self._schedule_refresh()
        except asyncio.TimeoutError:
            self._record_action(
                ActionResult(
                    ok=False,
                    action=action,
                    message=f"Action timed out after {self.action_timeout:.0f}s",
                )
            )
        finally:
            self.action_in_progress = False

    def _record_action(self, result: ActionResult) -> None:
        logs = self.query_one("#logs-view", RichLog)
        prefix = "OK" if result.ok else "ERR"
        ts = result.timestamp.astimezone(timezone.utc).strftime("%H:%M:%SZ")
        logs.write(f"[{ts}] [{prefix}] {result.action}: {result.message}")
        self.notify(result.message, severity="information" if result.ok else "error")

    def action_refresh(self) -> None:
        self._schedule_refresh()

    def action_proxy_up(self) -> None:
        self.run_worker(self._run_action("proxy_up"), group="actions")

    def action_proxy_down(self) -> None:
        def handle_confirm(confirmed: bool) -> None:
            if confirmed:
                self.run_worker(self._run_action("proxy_down"), group="actions")

        self.push_screen(
            ConfirmScreen("Confirm proxy stop", "Stop ingress proxy container now?"),
            handle_confirm,
        )

    def action_service_up(self) -> None:
        if not self.selected_service:
            self.notify("No service selected", severity="warning")
            return
        self.run_worker(self._run_action("service_up", target=self.selected_service), group="actions")

    def action_service_down(self) -> None:
        if not self.selected_service:
            self.notify("No service selected", severity="warning")
            return

        service_name = self.selected_service

        def handle_confirm(confirmed: bool) -> None:
            if confirmed:
                self.run_worker(self._run_action("service_down", target=service_name), group="actions")

        self.push_screen(
            ConfirmScreen("Confirm service stop", f"Stop service '{service_name}' now?"),
            handle_confirm,
        )

    def action_service_restart(self) -> None:
        if not self.selected_service:
            self.notify("No service selected", severity="warning")
            return

        service_name = self.selected_service

        def handle_confirm(confirmed: bool) -> None:
            if confirmed:
                self.run_worker(self._run_action("service_restart", target=service_name), group="actions")

        self.push_screen(
            ConfirmScreen("Confirm service restart", f"Restart service '{service_name}' now?"),
            handle_confirm,
        )

    def action_logs(self) -> None:
        if self.selected_service:
            self.run_worker(
                self._run_action("service_logs", target=self.selected_service, value=str(self.log_lines)),
                group="actions",
            )
        else:
            self.run_worker(self._run_action("proxy_logs", value=str(self.log_lines)), group="actions")

    def action_network_create(self) -> None:
        def handle_network_name(name: str | None) -> None:
            if not name:
                return
            self.run_worker(self._run_action("network_create", value=name), group="actions")

        self.push_screen(NetworkNameScreen(), handle_network_name)

    def action_cancel_action(self) -> None:
        if not self.action_in_progress:
            self.notify("No action in progress", severity="warning")
            return
        self._cancel_action_seq = self._action_seq
        self.notify("Cancellation requested", severity="warning")
