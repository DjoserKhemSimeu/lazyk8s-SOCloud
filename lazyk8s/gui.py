"""Textual-based GUI for lazyk8s"""

import subprocess
import shutil
import os
from pathlib import Path
from typing import List, Optional
from rich.text import Text
from textual import work, on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Footer, Static, ListView, ListItem, Label, RichLog, Input, Button,
    TabbedContent, TabPane
)
from textual.binding import Binding
from textual.reactive import reactive
from textual.timer import Timer
from kubernetes import client

from .k8s_client import K8sClient
from .config import AppConfig
from . import __version__

from lazyk8s.helpers.formatHelper import alignText


class StatusBar(Static):
    """Status bar displaying cluster info"""
    pass


class NamespaceItem(ListItem):
    """A list item for displaying a namespace"""

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        super().__init__(Label(f"  {namespace}"))


class ConfirmDialog(ModalScreen[bool]):
    """Modal screen for confirmation dialogs"""

    CSS = """
    ConfirmDialog {
        align: center middle;
        background: black 40%;
    }

    #confirm-dialog {
        width: 60;
        height: auto;
        border: round $error;
        background: $background;
        padding: 1 2;
    }

    #confirm-title {
        width: 100%;
        height: 1;
        content-align: center middle;
        color: $error;
        text-style: bold;
        padding: 0 0 1 0;
    }

    #confirm-message {
        width: 100%;
        height: auto;
        content-align: center middle;
        padding: 1 0;
    }

    #confirm-buttons {
        width: 100%;
        height: auto;
        align: center middle;
        padding: 1 0;
    }

    #confirm-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("n", "cancel", "No"),
        Binding("y", "confirm", "Yes"),
        # Vim navigation
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, message: str, title: str = "Confirm"):
        super().__init__()
        self.message = message
        self.title = title

    def compose(self) -> ComposeResult:
        with Container(id="confirm-dialog"):
            yield Static(self.title, id="confirm-title")
            yield Static(self.message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes (y)", variant="error", id="confirm-yes")
                yield Button("No (n)", variant="primary", id="confirm-no")

    def on_mount(self) -> None:
        """Focus the No button by default"""
        self.query_one("#confirm-no", Button).focus()

    @on(Button.Pressed, "#confirm-yes")
    def on_confirm_yes(self) -> None:
        """User confirmed"""
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-no")
    def on_confirm_no(self) -> None:
        """User cancelled"""
        self.dismiss(False)

    def action_confirm(self) -> None:
        """Confirm action"""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """Cancel action"""
        self.dismiss(False)


class UsernameInputDialog(ModalScreen[Optional[str]]):
    """Modal screen for inputting SSH username"""

    CSS = """
    UsernameInputDialog {
        align: center middle;
        background: black 40%;
    }

    #username-dialog {
        width: 60;
        height: auto;
        border: round $primary;
        background: $background;
        padding: 1 2;
    }

    #username-title {
        width: 100%;
        height: 1;
        content-align: center middle;
        color: $primary;
        text-style: bold;
        padding: 0 0 1 0;
    }

    #username-label {
        width: 100%;
        height: auto;
        padding: 1 0 0 0;
    }

    #username-input {
        width: 100%;
        margin: 1 0;
    }

    #username-buttons {
        width: 100%;
        height: auto;
        align: center middle;
        padding: 1 0;
    }

    #username-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        # Vim navigation
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, node_name: str):
        super().__init__()
        self.node_name = node_name

    def compose(self) -> ComposeResult:
        with Container(id="username-dialog"):
            yield Static("SSH Connection", id="username-title")
            yield Static(f"Enter username for node: {self.node_name}", id="username-label")
            yield Input(placeholder="Username (e.g., ubuntu, admin)", id="username-input")
            with Horizontal(id="username-buttons"):
                yield Button("Connect", variant="primary", id="username-connect")
                yield Button("Cancel", variant="default", id="username-cancel")

    def on_mount(self) -> None:
        """Focus the input field on mount"""
        self.query_one("#username-input", Input).focus()

    @on(Input.Submitted, "#username-input")
    def on_input_submitted(self) -> None:
        """Handle Enter key press in the input field"""
        username = self.query_one("#username-input", Input).value.strip()
        if username:
            self.dismiss(username)

    @on(Button.Pressed, "#username-connect")
    def on_connect(self) -> None:
        """User wants to connect"""
        username = self.query_one("#username-input", Input).value.strip()
        if username:
            self.dismiss(username)
        else:
            self.query_one("#username-input", Input).focus()

    @on(Button.Pressed, "#username-cancel")
    def on_cancel(self) -> None:
        """User cancelled"""
        self.dismiss(None)

    def action_cancel(self) -> None:
        """Cancel action"""
        self.dismiss(None)


class NamespaceSelector(ModalScreen[Optional[str]]):
    """Modal screen for selecting a namespace"""

    CSS = """
    NamespaceSelector {
        align: center middle;
        background: black 40%;
    }

    #namespace-dialog {
        width: 60;
        height: auto;
        max-height: 80%;
        border: round $accent;
        background: $background;
        padding: 1 2;
    }

    #namespace-filter-display {
        height: 1;
        color: $accent;
        padding: 0 0 0 0;
        margin: 0 0 1 0;
    }

    #namespace-list {
        height: auto;
        max-height: 20;
        min-height: 10;
        border: none;
        background: $surface 30%;
    }

    NamespaceItem {
        padding: 0 1;
        height: 1;

        &:hover {
            background: $boost;
        }
    }

    ListView > NamespaceItem.--highlight {
        background: $accent 30%;
    }

    #namespace-help {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        color: $text-muted;
        padding: 0 2;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+c", "cancel", "Cancel"),
        # Vim navigation
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, namespaces: List[str], current_namespace: str):
        super().__init__()
        self.all_namespaces = sorted(namespaces)
        self.current_namespace = current_namespace
        self.filtered_namespaces = self.all_namespaces.copy()
        self.filter_text = ""

    def compose(self) -> ComposeResult:
        with Container(id="namespace-dialog"):
            yield Static("Filter: ", id="namespace-filter-display")
            yield ListView(id="namespace-list")
            yield Static("↑↓/jk: Navigate | Enter: Select | Esc: Cancel | Type to filter", id="namespace-help")

    def on_mount(self) -> None:
        """Focus the list when mounted"""
        self.refresh_namespace_list()
        namespace_list = self.query_one("#namespace-list", ListView)
        namespace_list.focus()
        # Highlight the first item
        if len(namespace_list) > 0:
            namespace_list.index = 0

    def refresh_namespace_list(self) -> None:
        """Refresh the namespace list based on filter"""
        namespace_list = self.query_one("#namespace-list", ListView)
        namespace_list.clear()

        # Filter namespaces
        if self.filter_text:
            self.filtered_namespaces = [
                ns for ns in self.all_namespaces
                if self.filter_text.lower() in ns.lower()
            ]
        else:
            self.filtered_namespaces = self.all_namespaces.copy()

        # Add namespaces to list
        for ns in self.filtered_namespaces:
            namespace_list.append(NamespaceItem(ns))

        # Update filter display - always show it
        filter_display = self.query_one("#namespace-filter-display", Static)
        filter_display.update(f"Filter: {self.filter_text}")

        # Always highlight first item - use call_after_refresh to ensure it's applied
        def highlight_first():
            if len(namespace_list) > 0:
                namespace_list.index = 0
                namespace_list.focus()

        self.call_after_refresh(highlight_first)

    @on(ListView.Selected, "#namespace-list")
    def on_namespace_selected(self, event: ListView.Selected) -> None:
        """Handle namespace selection"""
        if isinstance(event.item, NamespaceItem):
            self.dismiss(event.item.namespace)

    def on_key(self, event) -> None:
        """Handle key presses for filtering"""
        key = event.key

        # Handle backspace
        if key == "backspace":
            if self.filter_text:
                self.filter_text = self.filter_text[:-1]
                self.refresh_namespace_list()
                event.prevent_default()
            return

        # Ignore special keys (including vim navigation)
        if key in ["escape", "enter", "up", "down", "left", "right", "tab",
                   "home", "end", "pageup", "pagedown", "ctrl+c", "j", "k", "h", "l"]:
            return

        # Handle character input (single char keys)
        if len(key) == 1 and key.isprintable():
            self.filter_text += key
            self.refresh_namespace_list()
            event.prevent_default()

    def action_cancel(self) -> None:
        """Cancel namespace selection"""
        self.dismiss(None)


class ContextItem(ListItem):
    """A list item for displaying a kubeconfig context"""

    def __init__(self, context: dict, is_current: bool = False) -> None:
        self.context = context
        self.context_name = context['name']
        self.is_current = is_current

        # Show indicator if current context
        indicator = "[green]●[/]" if is_current else " "
        super().__init__(Label(f"{indicator} {self.context_name}"))


class ClusterSelector(ModalScreen[Optional[str]]):
    """Modal screen for selecting a cluster context"""

    CSS = """
    ClusterSelector {
        align: center middle;
        background: black 40%;
    }

    #cluster-dialog {
        width: 70;
        height: auto;
        max-height: 80%;
        border: round $accent;
        background: $background;
        padding: 1 2;
    }

    #cluster-title {
        height: 1;
        color: $accent;
        text-style: bold;
        padding: 0 0 1 0;
    }

    #cluster-list {
        height: auto;
        max-height: 20;
        min-height: 10;
        border: none;
        background: $surface 30%;
    }

    ContextItem {
        padding: 0 1;
        height: 1;

        &:hover {
            background: $boost;
        }
    }

    ListView > ContextItem.--highlight {
        background: $accent 30%;
    }

    #cluster-help {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        color: $text-muted;
        padding: 0 2;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+c", "cancel", "Cancel"),
        # Vim navigation
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, contexts: List[dict], current_context: dict):
        super().__init__()
        self.contexts = contexts
        self.current_context = current_context

    def compose(self) -> ComposeResult:
        with Container(id="cluster-dialog"):
            yield Static("Select Cluster Context", id="cluster-title")
            yield ListView(id="cluster-list")
            yield Static("↑↓/jk: Navigate | Enter: Select | Esc: Cancel", id="cluster-help")

    def on_mount(self) -> None:
        """Populate the context list when mounted"""
        cluster_list = self.query_one("#cluster-list", ListView)

        current_name = self.current_context.get('name', '') if self.current_context else ''

        for ctx in self.contexts:
            is_current = ctx['name'] == current_name
            cluster_list.append(ContextItem(ctx, is_current))

        cluster_list.focus()
        if len(cluster_list) > 0:
            cluster_list.index = 0

    @on(ListView.Selected, "#cluster-list")
    def on_context_selected(self, event: ListView.Selected) -> None:
        """Handle context selection"""
        if isinstance(event.item, ContextItem):
            self.dismiss(event.item.context_name)

    def action_cancel(self) -> None:
        """Cancel context selection"""
        self.dismiss(None)


class NodeItem(ListItem):
    """A list item for displaying a node"""

    def __init__(self, node: client.V1Node, metrics: dict, pod_count: int, max_pods: int) -> None:
        self.node = node
        self.node_name = node.metadata.name

        # Get node status
        status = "Ready" if any(
            cond.type == "Ready" and cond.status == "True"
            for cond in node.status.conditions
        ) else "NotReady"
        status_icon = "[green]●[/]" if status == "Ready" else "[red]●[/]"

        # Pod count color
        pod_color = "green" if pod_count < max_pods * 0.8 else "yellow" if pod_count < max_pods else "red"

        # Get roles - check multiple label formats
        roles = []
        if node.metadata.labels:
            for label, value in node.metadata.labels.items():
                if 'node-role.kubernetes.io/' in label:
                    role = label.split('/')[-1]
                    if role:
                        roles.append(role)
                elif label == 'node-role.kubernetes.io/control-plane' or label == 'node-role.kubernetes.io/master':
                    if 'control-plane' not in roles and 'master' not in roles:
                        roles.append('control-plane')
        role_str = ",".join(roles) if roles else "worker"

        # CPU and Memory utilization
        cpu_str = ""
        mem_str = ""
        if node.metadata.name in metrics:
            node_metrics = metrics[node.metadata.name]
            cpu_percent = node_metrics['cpu_percent'].rstrip('%')
            mem_percent = node_metrics['memory_percent'].rstrip('%')

            try:
                cpu_val = float(cpu_percent)
                cpu_color = "green" if cpu_val < 70 else "yellow" if cpu_val < 90 else "red"
                cpu_str = f"[{cpu_color}]" + alignText(f"CPU:{cpu_percent}%", 12) + "[/]"
            except ValueError:
                cpu_str = alignText(f"CPU:{cpu_percent}%", 12)

            try:
                mem_val = float(mem_percent)
                mem_color = "green" if mem_val < 70 else "yellow" if mem_val < 90 else "red"
                mem_str = f"[{mem_color}]"+ alignText(f"Mem:{mem_percent}%", 12) + "[/]"
            except ValueError:
                mem_str = alignText(f"Mem:{mem_percent}%", 12)
        else:
            cpu_str = f"[dim]" + alignText("CPU:N/A", 12) + "[/]"
            mem_str = f"[dim]" + alignText("Mem:N/A", 12) + "[/]"

        podStr = alignText(f"{pod_count}/{max_pods}", 12, alignment='right')
        nameStr = alignText(self.node_name, 30, alignment='left', trimFromFront=True)

        label_text = f"{status_icon} {nameStr} {podStr}   {cpu_str} {mem_str} [dim]{role_str}[/]"
        super().__init__(Label(label_text))


class ClusterOverview(ModalScreen[bool]):
    """Modal screen for displaying cluster overview with node information"""

    CSS = """
    ClusterOverview {
        align: center middle;
        background: black 40%;
    }

    #overview-dialog {
        width: 90%;
        height: 90%;
        border: round $accent;
        background: $background;
        padding: 1 2;
    }

    #overview-title {
        height: 1;
        color: $accent;
        text-style: bold;
        padding: 0 0 1 0;
    }

    #overview-summary {
        height: auto;
        border: none;
        background: transparent;
        padding: 0 1;
    }

    #nodes-container {
        height: 20;
        margin-top: 1;
        border: round $accent 40%;
        background: $surface 30%;
        border-title-align: left;
        border-title-color: $text-accent 50%;
    }

    #nodes-list {
        height: 1fr;
        border: none;
        background: transparent;
        padding: 0 1;
    }

    NodeItem {
        padding: 0 1;
        height: 1;

        &:hover {
            background: $boost;
        }
    }

    ListView > NodeItem.--highlight {
        background: $accent 30%;
    }

    #node-details {
        height: 1fr;
        margin-top: 1;
        border: round $accent 40%;
        background: $surface 20%;
        border-title-align: left;
        border-title-color: $text-accent 50%;
    }

    #node-details-content {
        height: 1fr;
        border: none;
        background: transparent;
        padding: 1 2;
        overflow-y: auto;
    }

    #overview-help {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        color: $text-muted;
        padding: 0 2;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("ctrl+c", "close", "Close"),
        Binding("r", "refresh", "Refresh"),
        Binding("x", "ssh_node", "SSH"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, k8s_client: K8sClient):
        super().__init__()
        self.k8s_client = k8s_client
        self.nodes = []
        self.selected_node = None

    def compose(self) -> ComposeResult:
        with Container(id="overview-dialog"):
            yield Static("Cluster Overview", id="overview-title")
            yield Static(id="overview-summary")
            with Container(id="nodes-container"):
                yield ListView(id="nodes-list")
            with Container(id="node-details"):
                yield RichLog(id="node-details-content", highlight=True, markup=True)
            yield Static("↑↓/jk: Navigate | x: SSH | r: Refresh | Esc: Close", id="overview-help")

    def on_mount(self) -> None:
        """Load and display cluster overview"""
        self.query_one("#nodes-container").border_title = "Nodes"
        self.query_one("#node-details").border_title = "Node Details"
        self.refresh_overview()

    def refresh_overview(self) -> None:
        """Refresh the cluster overview data"""
        cluster_name, _ = self.k8s_client.get_cluster_info()
        self.nodes = self.k8s_client.get_nodes()
        if not self.nodes:
            summary = self.query_one("#overview-summary", Static)
            summary.update("[yellow]No nodes found[/]")
            return

        metrics = self.k8s_client.get_node_metrics()
        pod_counts = self.k8s_client.get_pod_count_per_node()

        total_pods = sum(pod_counts.values())
        summary = self.query_one("#overview-summary", Static)
        summary.update(f"[bold cyan]Cluster:[/] {cluster_name}  [bold cyan]Nodes:[/] {len(self.nodes)}  [bold cyan]Total Pods:[/] {total_pods}")

        nodes_list = self.query_one("#nodes-list", ListView)
        nodes_list.clear()

        for node in self.nodes:
            name = node.metadata.name
            pod_count = pod_counts.get(name, 0)
            max_pods = 110
            if node.status.allocatable and 'pods' in node.status.allocatable:
                max_pods = int(node.status.allocatable['pods'])
            nodes_list.append(NodeItem(node, metrics, pod_count, max_pods))

        nodes_list.focus()
        if len(nodes_list) > 0:
            nodes_list.index = 0

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Handle node selection"""
        if event.list_view.id == "nodes-list" and isinstance(event.item, NodeItem):
            self.selected_node = event.item.node
            self.show_node_details()

    def show_node_details(self) -> None:
        """Show detailed information about the selected node"""
        details_content = self.query_one("#node-details-content", RichLog)
        details_content.clear()

        if not self.selected_node:
            details_content.write("[dim]No node selected[/]")
            return

        node = self.selected_node
        details_content.write(f"[bold cyan]Name:[/] {node.metadata.name}")

        if node.status.addresses:
            details_content.write(f"[bold cyan]Addresses:[/]")
            for addr in node.status.addresses:
                details_content.write(f"  {addr.type}: [green]{addr.address}[/]")
        details_content.write("")

        details_content.write(f"[bold cyan]Status:[/]")
        for cond in node.status.conditions:
            status_color = "green" if cond.status == "True" else "red"
            details_content.write(f"  {cond.type}: [{status_color}]{cond.status}[/]")
        details_content.write("")

        if node.status.node_info:
            info = node.status.node_info
            details_content.write(f"[bold cyan]System Info:[/]")
            details_content.write(f"  Kubelet: {info.kubelet_version}")
            details_content.write(f"  OS: {info.operating_system}")
            details_content.write(f"  OS Image: {info.os_image}")
            details_content.write(f"  Kernel: {info.kernel_version}")
            details_content.write(f"  Container Runtime: {info.container_runtime_version}")
            details_content.write("")

        if node.status.capacity:
            details_content.write(f"[bold cyan]Capacity:[/]")
            details_content.write(f"  CPU: {node.status.capacity.get('cpu', 'N/A')}")
            details_content.write(f"  Memory: {node.status.capacity.get('memory', 'N/A')}")
            details_content.write(f"  Pods: {node.status.capacity.get('pods', 'N/A')}")
            details_content.write("")

        if node.status.allocatable:
            details_content.write(f"[bold cyan]Allocatable:[/]")
            details_content.write(f"  CPU: {node.status.allocatable.get('cpu', 'N/A')}")
            details_content.write(f"  Memory: {node.status.allocatable.get('memory', 'N/A')}")
            details_content.write(f"  Pods: {node.status.allocatable.get('pods', 'N/A')}")
            details_content.write("")

        if node.metadata.labels:
            details_content.write(f"[bold cyan]Labels:[/]")
            for key, value in sorted(node.metadata.labels.items()):
                details_content.write(f"  [yellow]{key}[/]: {value}")

    def action_refresh(self) -> None:
        """Refresh the overview"""
        self.refresh_overview()

    async def action_ssh_node(self) -> None:
        """SSH into the selected node"""
        if not self.selected_node:
            return

        node = self.selected_node
        ip_address = None
        if node.status.addresses:
            for addr in node.status.addresses:
                if addr.type == "ExternalIP":
                    ip_address = addr.address
                    break
            if not ip_address:
                for addr in node.status.addresses:
                    if addr.type == "InternalIP":
                        ip_address = addr.address
                        break

        if not ip_address:
            return

        username = await self.app.push_screen(UsernameInputDialog(node.metadata.name))
        if not username:
            return

        with self.app.suspend():
            separator = "─" * 60
            print(f"\033[36m{separator}\033[0m")
            print(f"\033[36m→ \033[1;37mSSH to Node\033[0m")
            print(f"  \033[2mNode:\033[0m \033[32m{node.metadata.name}\033[0m")
            print(f"  \033[2mUser:\033[0m \033[33m{username}\033[0m")
            print(f"  \033[2mIP:\033[0m \033[35m{ip_address}\033[0m")
            print(f"\033[36m{separator}\033[0m\n")

            try:
                subprocess.run(["ssh", f"{username}@{ip_address}"])
            except Exception as e:
                print(f"\n\033[31mSSH failed: {e}\033[0m")

            print(f"\n\033[36m{separator}\033[0m")
            print(f"\033[36m← \033[1;37mExited SSH\033[0m")
            print(f"\033[2mPress \033[0m\033[1;32mEnter\033[0m\033[2m to return to \033[0m\033[1;36mlazyk8s\033[0m\033[2m...\033[0m")
            print(f"\033[36m{separator}\033[0m")
            input()

    def action_close(self) -> None:
        """Close the overview"""
        self.dismiss(True)


class PodItem(ListItem):
    """A list item for displaying a pod"""

    def __init__(self, pod: client.V1Pod, k8s_client: K8sClient) -> None:
        self.pod = pod
        self.k8s_client = k8s_client
        status = k8s_client.get_pod_status(pod)

        phase = pod.status.phase
        if phase == "Running":
            ready = sum(1 for cs in (pod.status.container_statuses or []) if cs.ready)
            total = len(pod.status.container_statuses or [])
            icon = "[green]●[/]" if ready == total and total > 0 else "[yellow]●[/]"
        elif phase == "Pending":
            icon = "[yellow]●[/]"
        else:
            icon = "[red]●[/]"

        label_text = f"{icon} {pod.metadata.name}"
        super().__init__(Label(label_text))


class ContainerItem(ListItem):
    """A list item for displaying a container"""

    def __init__(self, container_name: str, is_active: bool = False) -> None:
        self.container_name = container_name
        self.is_active = is_active
        indicator = "[green]●[/]" if is_active else "[dim]○[/]"
        super().__init__(Label(f"{indicator} {container_name}"))

    def update_active_state(self, is_active: bool) -> None:
        """Update the active state of the container"""
        self.is_active = is_active
        indicator = "[green]●[/]" if is_active else "[dim]○[/]"
        label = self.query_one(Label)
        label.update(f"{indicator} {self.container_name}")


class LazyK8sApp(App):
    """Textual TUI for Kubernetes management"""

    THEME = "tokyo-night"

    CSS = """
    * {
        scrollbar-color: $primary 30%;
        scrollbar-color-hover: $primary 60%;
        scrollbar-color-active: $primary;
        scrollbar-background: $surface;
        scrollbar-background-hover: $surface;
        scrollbar-background-active: $surface;
        scrollbar-size-vertical: 1;
    }

    Screen {
        background: $background;
    }

    StatusBar {
        dock: top;
        height: 1;
        background: $surface;
        color: $text;
        padding: 0 2;
    }

    #main-container {
        layout: horizontal;
        height: 1fr;
        padding: 0 1;
    }

    #left-panel {
        width: 35%;
        height: 1fr;
    }

    #pods-container {
        height: 1fr;
        border: round $accent 40%;
        background: $surface 30%;
        border-title-align: left;
        border-title-color: $text-accent 50%;

        &:focus-within {
            border: round $accent 100%;
            border-title-color: $text;
            border-title-style: bold;
        }
    }

    #pods-list {
        height: 1fr;
        border: none;
        background: transparent;
        padding: 0 1;
    }

    #containers-container {
        height: 7;
        margin-top: 1;
        border: round $accent 40%;
        background: $surface 30%;
        border-title-align: left;
        border-title-color: $text-accent 50%;

        &:focus-within {
            border: round $accent 100%;
            border-title-color: $text;
            border-title-style: bold;
        }
    }

    #containers-list {
        height: 5;
        border: none;
        background: transparent;
        padding: 0 1;
    }

    #containers-list ListItem {
        padding: 0 1;
    }

    #right-panel {
        width: 65%;
        height: 1fr;
        margin-left: 1;
    }

    #info-container {
        height: auto;
        border: round $accent 40%;
        background: $surface 20%;
        border-title-align: left;
        border-title-color: $text-accent 50%;
    }

    #info-panel {
        height: auto;
        max-height: 10;
        border: none;
        background: transparent;
        padding: 1 2;
        color: $text;
    }

    #logs-container {
        height: 1fr;
        margin-top: 1;
        border: round $accent 40%;
        background: $surface 20%;
        border-title-align: left;
        border-title-color: $text-accent 50%;

        &:focus-within {
            border: round $accent 100%;
            border-title-color: $text;
            border-title-style: bold;
        }
    }

    #logs-tabs {
        height: 1fr;
        background: transparent;
    }

    #logs-tabs Tabs {
        height: 1;
        dock: top;
        background: transparent;
    }

    #logs-tabs Tab {
        display: none;
    }

    #logs-tabs Underline {
        display: none;
    }

    #logs-tabs TabPane {
        padding: 0;
    }

    #logs-panel, #events-panel, #metadata-panel {
        height: 1fr;
        border: none;
        background: transparent;
        padding: 0 1;
        overflow-x: auto;
        overflow-y: auto;
    }

    RichLog {
        scrollbar-size-horizontal: 1;
    }

    ListView {
        height: 100%;
        padding: 0;
    }

    ListItem {
        padding: 0 1;
        height: 1;

        &:hover {
            background: $boost;
        }
    }

    .panel-title {
        color: $text-accent 60%;
        text-align: right;
        padding: 0 1;
    }

    Footer {
        background: $surface;
        padding-left: 2;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("a", "open_alumet", "Alumet"),
        Binding("n", "change_namespace", "Namespace"),
        Binding("c", "cluster_overview", "Cluster"),
        Binding("x", "open_shell", "Shell"),
        Binding("f", "toggle_follow", "Follow"),
        Binding("d", "delete_pod", "Delete"),
        Binding("space", "toggle_container", "Toggle Container", show=False),
        Binding("tab", "focus_next", "Next"),
        Binding("L", "switch_tab('logs-tab')", "Logs", show=False),
        Binding("E", "switch_tab('events-tab')", "Events", show=False),
        Binding("M", "switch_tab('metadata-tab')", "Metadata", show=False),
        Binding("h", "scroll_log_left", "Scroll Left", show=False),
        Binding("l", "scroll_log_right", "Scroll Right", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("left", "scroll_log_left", "Scroll Left", show=False),
        Binding("right", "scroll_log_right", "Scroll Right", show=False),
    ]

    selected_pod: reactive[Optional[client.V1Pod]] = reactive(None)
    selected_container: reactive[Optional[str]] = reactive(None)
    current_namespace: reactive[str] = reactive("default")
    following_logs: reactive[bool] = reactive(False)
    show_alumet: reactive[bool] = reactive(False)

    def __init__(self, k8s_client: K8sClient, app_config: AppConfig):
        super().__init__()
        self.k8s_client = k8s_client
        self.app_config = app_config
        self.pods: List[client.V1Pod] = []
        self.current_namespace = k8s_client.get_current_namespace()
        self._debounce_timer: Optional[Timer] = None
        self._pending_pod_index: Optional[int] = None
        self._log_follow_timer: Optional[Timer] = None
        self.active_containers: set[str] = set()

    def compose(self) -> ComposeResult:
        """Create child widgets"""
        yield StatusBar(id="status-bar")

        with Horizontal(id="main-container"):
            with Vertical(id="left-panel"):
                with Container(id="pods-container"):
                    yield ListView(id="pods-list")
                with Container(id="containers-container"):
                    yield ListView(id="containers-list")

            with Vertical(id="right-panel"):
                with Container(id="info-container"):
                    yield Static(id="info-panel")
                with Container(id="logs-container"):
                    with TabbedContent(id="logs-tabs"):
                        with TabPane("Logs", id="logs-tab"):
                            yield RichLog(id="logs-panel", highlight=True, markup=True)
                        with TabPane("Events", id="events-tab"):
                            yield RichLog(id="events-panel", highlight=True, markup=True)
                        with TabPane("Metadata", id="metadata-tab"):
                            yield RichLog(id="metadata-panel", highlight=True, markup=True)
                    # FIX: Un seul panneau alumet-panel déclaré ici (celui du TabbedContent a été supprimé)
                    yield RichLog(id="alumet-panel", highlight=False, markup=True)
        yield Footer()




    def debug_log(self, message: str, level: str = "INFO") -> None:
        """Écrit un message de débug de manière sécurisée si le panneau est prêt."""
        # Sécurité : Si l'application n'est pas pleinement active, on écrit dans la console de dev standard
        if not getattr(self, "is_running", False):
            self.log(f"[{level.upper()}] {message}")
            return

        colors = {"INFO": "cyan", "WARN": "yellow", "ERROR": "red", "SUCCESS": "green"}
        color = colors.get(level.upper(), "white")
        
        from datetime import datetime
        now = datetime.now().strftime("%H:%M:%S")
        formatted_msg = f"[{color}][DEBUG {now}] [{level.upper()}] {message}[/{color}]"
        
        try:
            # On vérifie d'abord si le panel est monté pour éviter le crash synchrone
            panel = self.query_one("#alumet-panel", RichLog)
            if panel:
                self.call_from_thread(panel.write, formatted_msg)
        except Exception:
            # Fallback vers la console invisible si le widget n'est pas encore accessible
            self.log(formatted_msg)


    alumet_node_data = {}
    alumet_lock = __import__("threading").Lock()
    alumet_last_seen_rapl = {}

    @work(exclusive=True, thread=True)
    def start_alumet_stream(self) -> None:
        """Déclenche le streaming multi-nœuds sans bloquer l'application."""
        import subprocess
        import threading
        import time

        time.sleep(1)
        self.safe_alumet_write("[bold yellow]🚀 Initialisation du moniteur multi-nœuds Alumet...[/]")

        cmd = ["kubectl", "get", "pods", "-o", "custom-columns=NAME:.metadata.name,NODE:.spec.nodeName", "--no-headers"]
        try:
            output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
            count = 0
            for line in output.split('\n'):
                self.debug_log("Recherche des pods Alumet lancée...")
                if "alumet-relay-client" in line and len(line.split()) == 2:
                    p_name, n_name = line.split()
                    self.debug_log(f"Pod trouvé : {p_name}", level="SUCCESS")
                    self.debug_log(f"Pod trouvé : {p_name}", level="SUCCESS")
                    threading.Thread(target=self._stream_alumet_node_data, args=(p_name, n_name), daemon=True).start()
                    count += 1
            
            if count == 0:
                self.safe_alumet_write("[red]❌ Aucun pod 'alumet-relay-client' détecté.[/]")
            else:
                self.safe_alumet_write(f"[green]✅ {count} flux Alumet nœud/pod démarrés avec succès.[/]\n")
                
                # FIX CRITIQUE : Au lieu d'un 'while True' bloquant, on délègue le rafraîchissement
                # à un intervalle natif de Textual, géré proprement par l'Event Loop.
                self.call_from_thread(self.set_interval, 1.0, self._trigger_alumet_refresh)

        except Exception as e:
            self.safe_alumet_write(f"[red]❌ Erreur d'initialisation : {e}[/]")

    def _trigger_alumet_refresh(self) -> None:
        """Déclenché toutes les secondes par l'intervalle Textual."""
        if getattr(self, "show_alumet", False):
            self._refresh_alumet_display()
    def _stream_alumet_node_data(self, pod_name: str, node_name: str) -> None:
        import subprocess
        from datetime import datetime, timezone
        
        metrics_list = [
            "rapl_consumed_energy","grace_instant_power", "cpu_percent", "memory_usage",
            "cgroup_memory_anonymous", "cgroup_memory_file",
            "cgroup_memory_kernel_stack", "cgroup_memory_pagetables",
            "nvml_instant_power", "nvml_temperature_gpu", 
            "nvml_gpu_utilization", "nvml_memory_utilization"
        ]

        def parse_ts(ts_str):
            try:
                return datetime.fromisoformat(ts_str.replace('Z', '')).replace(tzinfo=timezone.utc).timestamp()
            except: return __import__("time").time()

        cmd = ["kubectl", "exec", "-i", pod_name, "--", "stdbuf", "-oL", "tail", "-f", "/tmp/energy_data.csv"]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        
        while True:
            line = process.stdout.readline()
            if not line: break
            line = line.strip()
            if ";" not in line:
                if "ERROR" in line or "WARN" in line:
                    self.safe_alumet_write(f"[bold red][POD LOG] {line}[/bold red]")
                continue
            
            parts = line.split(";")
            if len(parts) < 3: continue
            
            m_name, ts_raw, val_raw = parts[0], parts[1], parts[2]
            try: val = float(val_raw)
            except: continue
            curr_ts = parse_ts(ts_raw)
            
            with self.alumet_lock:
                if node_name not in self.alumet_node_data:
                    self.alumet_node_data[node_name] = {m: {'curr': 0.0} for m in metrics_list}
                    self.alumet_node_data[node_name]['pods_cpu_usage'] = {}
                    self.alumet_node_data[node_name]['gpus'] = {}
                    # Track which metric source is used for power (rapl or grace-hopper)
                    self.alumet_node_data[node_name]['power_source'] = 'unknown'

                if "rapl_consumed_energy" in m_name and "domain=package_total" in line:
                    m_id = f"{node_name}_package_total"
                    if m_id in self.alumet_last_seen_rapl:
                        p_ts, _ = self.alumet_last_seen_rapl[m_id]
                        dt = curr_ts - p_ts
                        if 0.001 < dt < 5.0:
                                watts = val / dt
                                if 0 <= watts < 1000.0:
                                    self.alumet_node_data[node_name]['rapl_consumed_energy']['curr'] = watts
                                    self.alumet_node_data[node_name]['power_source'] = 'rapl'
                    self.alumet_last_seen_rapl[m_id] = (curr_ts, val)

                # Fallback: if RAPL energy isn't available, use graceful instantaneous
                # power reported by 'grace_instant_power' (assumed in Watts).
                elif "grace_instant_power" in m_name and "domain=package_total" in line:
                    try:
                        # Accept reasonable watt values and set as current power
                        if 0.0 <= val < 10000.0:
                            self.alumet_node_data[node_name]['rapl_consumed_energy']['curr'] = val
                            self.alumet_node_data[node_name]['power_source'] = 'grace-hopper'
                    except Exception:
                        pass

                elif "nvml_" in m_name:
                    gpu_id = parts[4] if len(parts) > 4 else "0"
                    if gpu_id not in self.alumet_node_data[node_name]['gpus']:
                        self.alumet_node_data[node_name]['gpus'][gpu_id] = {m: {'curr': 0.0} for m in metrics_list if "nvml" in m}
                    
                    f_val = val / 1000.0 if "power" in m_name else val
                    for m_key in metrics_list:
                        if m_key in m_name:
                            self.alumet_node_data[node_name]['gpus'][gpu_id][m_key]['curr'] = f_val
                            break

                elif "cpu_percent" in m_name and "kind=total" in line:
                    try:
                        p_name = line.split("name=")[1].split(",")[0]
                        self.alumet_node_data[node_name]['pods_cpu_usage'][p_name] = val
                    except: pass            

                else:
                    for m_key in ["memory_usage", "cgroup_memory_anonymous", "cgroup_memory_file", "cgroup_memory_kernel_stack", "cgroup_memory_pagetables"]:
                        if m_name.startswith(m_key):
                            self.alumet_node_data[node_name][m_key]['curr'] = val / (1024**2)
                            break

    def _refresh_alumet_display(self) -> None:
        """Génère le rendu visuel textuel propre à partir des données collectées."""
        try:
            panel = self.query_one("#alumet-panel", RichLog)
            panel.clear()

            with self.alumet_lock:
                if not self.alumet_node_data:
                    panel.write("[bold blink cyan] ATTENTE DE DONNÉES ALUMET DES NŒUDS...[/]")
                    return

                for node, data in sorted(self.alumet_node_data.items()):
                    panel.write(f"[bold reverse cyan]   NODE: {node} [/bold reverse cyan]")
                    
                    p_curr = data['rapl_consumed_energy']['curr']
                    source = data.get('power_source', 'unknown')
                    if source == 'grace-hopper':
                        panel.write(f"  [bold yellow] TOTAL POWER (grace-hopper):[/] [green]{p_curr:>6.2f} W[/green]")
                    elif source == 'rapl':
                        panel.write(f"  [bold yellow] TOTAL POWER (RAPL):[/] [green]{p_curr:>6.2f} W[/green]")
                    else:
                        panel.write(f"  [bold yellow] TOTAL POWER:[/] [green]{p_curr:>6.2f} W[/green]")
                    
                    panel.write("  [bold blue] DETAILED MEMORY (MiB):[/]")
                    mem_keys = ["cgroup_memory_anonymous", "cgroup_memory_file", "cgroup_memory_kernel_stack", "cgroup_memory_pagetables"]
                    mem_line = "    " + " | ".join([f"{m[14:].upper()}: [bold]{data[m]['curr']:.2f}[/] MiB" for m in mem_keys if m in data])
                    panel.write(mem_line)

                    gpus = data.get('gpus', {})
                    if gpus:
                        panel.write("  [bold magenta] GPU METRICS:[/]")
                        for g_id, g_metrics in sorted(gpus.items()):
                            pwr = g_metrics.get('nvml_instant_power', {}).get('curr', 0.0)
                            tmp = g_metrics.get('nvml_temperature_gpu', {}).get('curr', 0.0)
                            util = g_metrics.get('nvml_gpu_utilization', {}).get('curr', 0.0)
                            mem = g_metrics.get('nvml_memory_utilization', {}).get('curr', 0.0)
                            panel.write(f"    └─ [bold]ID {g_id[-12:]}:[/] Power: [green]{pwr:.1f}W[/] | Temp: {tmp:.1f}°C | Util: [cyan]{util:.1f}%[/] | Mem: {mem:.1f}%")

                    pods = sorted(data.get('pods_cpu_usage', {}).items(), key=lambda x: x[1], reverse=True)[:4]
                    if pods:
                        panel.write("  [bold orange3] TOP PODS (CPU%):[/]")
                        for p_name, p_val in pods:
                            panel.write(f"    ├─ {p_name[:30]:30} [cyan]{p_val:>5.1f}%[/cyan]")
                    
                    panel.write("─" * 50)
        except Exception:
            pass

    def safe_alumet_write(self, msg: str) -> None:
        try: self.call_from_thread(self.query_one("#alumet-panel", RichLog).write, msg)
        except: pass

    def update_alumet_ui(self, text: str) -> None:
        panel = self.query_one("#alumet-panel", RichLog)
        panel.write(text)

    def write_alumet_error(self, text: str) -> None:
        self.query_one("#alumet-panel", RichLog).write(text)

    def watch_show_alumet(self, show_alumet: bool) -> None:
        """Bascule visuellement et de manière forcée entre K8s et Alumet."""
        try:
            tabs = self.query_one("#logs-tabs")
            alumet_panel = self.query_one("#alumet-panel")
            container = self.query_one("#logs-container")

            if show_alumet:
                tabs.display = False
                alumet_panel.display = True
                container.border_title = "[bold green]ALUMET ENERGY MONITOR FIELD (Press 'a' to exit)[/]"
                
                # On force un premier texte pour prouver que le panneau est ouvert
                alumet_panel.clear()
                alumet_panel.write("[bold yellow]⏳ Connexion au flux Alumet en cours...[/]")
                self._refresh_alumet_display()
            else:
                tabs.display = True
                alumet_panel.display = False
                self.update_logs_title()
        except Exception:
            pass

    def on_mount(self) -> None:
        """Called when app is mounted"""
        self.title = "lazyk8s"

        self.query_one("#pods-container").border_title = "Pods"
        self.query_one("#containers-container").border_title = "Containers [dim](Space to toggle)[/]"
        self.query_one("#info-container").border_title = "Info"
        self.update_logs_title()

        # FIX: Masquer le panneau Alumet par défaut au démarrage
        self.query_one("#alumet-panel").display = False

        self.refresh_status_bar()
        self.refresh_pods()

        if self.pods:
            self.selected_pod = self.pods[0]
            self.refresh_containers()
            self.show_pod_info()
            self.show_pod_logs()
            self.show_pod_events()
            self.show_pod_metadata()

        # FIX CRITIQUE : Lancement automatique du flux en tâche de fond
        self.start_alumet_stream()

    def refresh_status_bar(self) -> None:
        host, _ = self.k8s_client.get_cluster_info()
        namespace = self.k8s_client.get_current_namespace()
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.update(
            f"[b]lazyk8s[/] [dim]v{__version__}[/]  [cyan]●[/] {host}  [cyan]●[/] {namespace}"
        )

    def refresh_pods(self) -> None:
        self.pods = self.k8s_client.get_pods()
        pods_list = self.query_one("#pods-list", ListView)
        pods_list.clear()
        for pod in self.pods:
            pods_list.append(PodItem(pod, self.k8s_client))

    def refresh_containers(self) -> None:
        containers_list = self.query_one("#containers-list", ListView)
        containers_list.clear()
        if self.selected_pod:
            containers = self.k8s_client.get_container_names(self.selected_pod)
            if not self.active_containers and containers:
                self.active_containers = set(containers)
            for container in containers:
                is_active = container in self.active_containers
                containers_list.append(ContainerItem(container, is_active))

    def show_pod_info(self) -> None:
        info_panel = self.query_one("#info-panel", Static)
        if not self.selected_pod:
            info_panel.update("[dim]no pod selected[/]")
            return
        pod = self.selected_pod
        info_lines = [
            f"[b]{pod.metadata.name}[/]",
            f"[dim]node:[/] {pod.spec.node_name or 'n/a'}  [dim]ip:[/] {pod.status.pod_ip or 'n/a'}",
            "",
        ]
        for container in pod.spec.containers:
            info_lines.append(f"[cyan]●[/] {container.name}")
            info_lines.append(f"  [dim]{container.image}[/]")
        info_panel.update("\n".join(info_lines))

    def show_pod_logs(self) -> None:
        logs_panel = self.query_one("#logs-panel", RichLog)
        logs_panel.clear()
        if not self.selected_pod:
            logs_panel.write("[dim]no pod selected[/]")
            return
        containers = self.k8s_client.get_container_names(self.selected_pod)
        if not containers:
            logs_panel.write("[dim]no containers found[/]")
            return
        active = [c for c in containers if c in self.active_containers]
        if not active:
            logs_panel.write("[dim]no active containers (press Space to toggle)[/]")
            return
        if len(active) == 1:
            logs = self.k8s_client.get_pod_logs(self.selected_pod.metadata.name, active[0], lines=100)
            self._write_logs(logs_panel, logs, None)
        else:
            logs = self.k8s_client.get_pod_logs_all_containers(self.selected_pod.metadata.name, active, lines=100)
            self._write_prefixed_logs(logs_panel, logs)

    def _write_logs(self, logs_panel: RichLog, logs: Optional[str], container_name: Optional[str]) -> None:
        """Write logs with colorization, checking if logs are available."""
        # FIX : Si Kubernetes n'a renvoyé aucun log (None), on affiche un message d'attente au lieu de crash
        if logs is None:
            logs_panel.write("[yellow]⏳ En attente des logs du conteneur (Pod en cours d'initialisation ou indisponible)...[/]")
            return

        for line in logs.split("\n"):
            if line:
                if any(level in line.upper() for level in ["ERROR", "FATAL"]):
                    logs_panel.write(f"[red]{line}[/]")
                elif any(level in line.upper() for level in ["WARN", "WARNING"]):
                    logs_panel.write(f"[yellow]{line}[/]")
                else:
                    logs_panel.write(line)

    def _write_prefixed_logs(self, logs_panel: RichLog, logs: Optional[str]) -> None:
        """Write prefixed logs, checking if logs are available."""
        # FIX : Même sécurité si le pod possède plusieurs conteneurs
        if logs is None:
            logs_panel.write("[yellow]⏳ En attente des logs combinés...[/]")
            return

        for line in logs.split("\n"):
            if not line: continue
            if line.startswith("["):
                try:
                    prefix_end = line.index("]")
                    prefix = line[1:prefix_end]
                    container_name = prefix.split("/")[1] if "/" in prefix else prefix
                    rest = line[prefix_end + 1:].strip()
                    log_message = rest.split(" ", 1)[1] if " " in rest else rest
                    container_tag = f"[cyan]{container_name}[/]"

                    if any(level in log_message.upper() for level in ["ERROR", "FATAL"]):
                        logs_panel.write(f"{container_tag} [red]{log_message}[/]")
                    elif any(level in log_message.upper() for level in ["WARN", "WARNING"]):
                        logs_panel.write(f"{container_tag} [yellow]{log_message}[/]")
                    else:
                        logs_panel.write(f"{container_tag} {log_message}")
                except (ValueError, IndexError):
                    logs_panel.write(line)
            else:
                logs_panel.write(line)
    def show_pod_events(self) -> None:
        events_panel = self.query_one("#events-panel", RichLog)
        events_panel.clear()
        if not self.selected_pod:
            events_panel.write("[dim]no pod selected[/]")
            return
        events = self.k8s_client.get_pod_events(self.selected_pod.metadata.name)
        if not events or events.strip() == "":
            events_panel.write("[dim]no events found[/]")
            return
        for line in events.split("\n"):
            if not line.strip(): continue
            line_lower = line.lower()
            if "warning" in line_lower or "failed" in line_lower or "error" in line_lower:
                events_panel.write(f"[yellow]{line}[/]")
            elif "backoff" in line_lower or "killing" in line_lower:
                events_panel.write(f"[red]{line}[/]")
            elif "pulled" in line_lower or "created" in line_lower or "started" in line_lower:
                events_panel.write(f"[green]{line}[/]")
            else:
                events_panel.write(line)

    def show_pod_metadata(self) -> None:
        metadata_panel = self.query_one("#metadata-panel", RichLog)
        metadata_panel.clear()
        if not self.selected_pod:
            metadata_panel.write("[dim]no pod selected[/]")
            return
        pod = self.selected_pod
        metadata_panel.write(f"[bold cyan]Basic Information[/]")
        metadata_panel.write(f"  Name: [green]{pod.metadata.name}[/]")
        metadata_panel.write(f"  Namespace: [green]{pod.metadata.namespace}[/]")
        metadata_panel.write(f"  UID: [dim]{pod.metadata.uid}[/]")
        metadata_panel.write(f"  Created: {pod.metadata.creation_timestamp}\n")

        if pod.metadata.labels:
            metadata_panel.write(f"[bold cyan]Labels[/]")
            for key, value in sorted(pod.metadata.labels.items()):
                metadata_panel.write(f"  [yellow]{key}[/]: {value}")
            metadata_panel.write("")

        if pod.metadata.annotations:
            metadata_panel.write(f"[bold cyan]Annotations[/]")
            for key, value in sorted(pod.metadata.annotations.items()):
                if len(value) > 100: value = value[:97] + "..."
                metadata_panel.write(f"  [yellow]{key}[/]: [dim]{value}[/]")
            metadata_panel.write("")

        metadata_panel.write(f"[bold cyan]Spec[/]")
        metadata_panel.write(f"  Node: {pod.spec.node_name or 'N/A'}")
        metadata_panel.write(f"  Service Account: {pod.spec.service_account or 'default'}")
        metadata_panel.write(f"  Restart Policy: {pod.spec.restart_policy}\n")

        metadata_panel.write(f"[bold cyan]Status[/]")
        metadata_panel.write(f"  Phase: {pod.status.phase}")
        metadata_panel.write(f"  Pod IP: {pod.status.pod_ip or 'N/A'}")
        metadata_panel.write(f"  QoS Class: {pod.status.qos_class or 'N/A'}")

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id == "pods-list":
            if self._debounce_timer is not None:
                self._debounce_timer.stop()
            if event.item is not None and isinstance(event.item, PodItem):
                self._pending_pod_index = self.pods.index(event.item.pod)
                self._debounce_timer = self.set_timer(0.2, self._select_pending_pod)

    def _select_pending_pod(self) -> None:
        if self._pending_pod_index is not None and self._pending_pod_index < len(self.pods):
            self.selected_pod = self.pods[self._pending_pod_index]
            self.selected_container = None
            self.active_containers.clear()
            self.refresh_containers()
            self.show_pod_info()
            self.show_pod_logs()
            self.show_pod_events()
            self.show_pod_metadata()
            self._pending_pod_index = None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "pods-list":
            if self._debounce_timer is not None:
                self._debounce_timer.stop()
            if isinstance(event.item, PodItem):
                self.selected_pod = event.item.pod
                self.selected_container = None
                self.active_containers.clear()
                self.refresh_containers()
                self.show_pod_info()
                self.show_pod_logs()
                self.show_pod_events()
                self.show_pod_metadata()
        elif event.list_view.id == "containers-list":
            if isinstance(event.item, ContainerItem):
                self.selected_container = event.item.container_name

    def action_refresh(self) -> None:
        self.refresh_pods()
        if self.selected_pod:
            self.refresh_containers()
            self.show_pod_info()
            self.show_pod_logs()

    def action_change_namespace(self) -> None:
        namespaces = self.k8s_client.get_namespaces()
        current_namespace = self.k8s_client.get_current_namespace()

        def handle_namespace_selection(selected_namespace: Optional[str]) -> None:
            if selected_namespace and selected_namespace != current_namespace:
                self.k8s_client.set_namespace(selected_namespace)
                self.current_namespace = selected_namespace
                self.refresh_status_bar()
                self.refresh_pods()
                if self.pods:
                    self.selected_pod = self.pods[0]
                    self.refresh_containers()
                    self.show_pod_info()
                    self.show_pod_logs()
                else:
                    self.selected_pod = None
                    self.refresh_containers()
                    self.show_pod_info()
                    self.show_pod_logs()

        self.push_screen(NamespaceSelector(namespaces, current_namespace), handle_namespace_selection)

    def action_cluster_overview(self) -> None:
        contexts, current_context = self.k8s_client.get_contexts()
        if len(contexts) > 1:
            def handle_context_selection(selected_context: Optional[str]) -> None:
                if selected_context and selected_context != current_context.get('name', ''):
                    success = self.k8s_client.switch_context(selected_context)
                    if success:
                        self.refresh_status_bar()
                        self.refresh_pods()
                        self.selected_pod = None
                        self.selected_container = None
                        self.active_containers.clear()
                        self.refresh_containers()
                        self.show_pod_info()
                        self.show_pod_logs()
                self.push_screen(ClusterOverview(self.k8s_client))
            self.push_screen(ClusterSelector(contexts, current_context), handle_context_selection)
        else:
            self.push_screen(ClusterOverview(self.k8s_client))

    def action_view_logs(self) -> None:
        if self.selected_pod: self.show_pod_logs()

    def update_logs_title(self) -> None:
        try:
            logs_tabs = self.query_one("#logs-tabs", TabbedContent)
            active_tab = logs_tabs.active
            if active_tab == "logs-tab":
                title = "[cyan](L)ogs[/] | [dim](E)vents[/] | [dim](M)etadata[/]"
            elif active_tab == "events-tab":
                title = "[dim](L)ogs[/] | [cyan](E)vents[/] | [dim](M)etadata[/]"
            elif active_tab == "metadata-tab":
                title = "[dim](L)ogs[/] | [dim](E)vents[/] | [cyan](M)etadata[/]"
            else:
                title = "(L)ogs | (E)vents | (M)etadata"
            if self.following_logs and active_tab == "logs-tab":
                title = title.replace("(L)ogs", "(L)ogs [green]●[/]")
            self.query_one("#logs-container").border_title = title
        except Exception: pass

    def action_switch_tab(self, tab_id: str) -> None:
        try:
            logs_tabs = self.query_one("#logs-tabs", TabbedContent)
            logs_tabs.active = tab_id
            self.update_logs_title()
        except Exception: pass

    def action_scroll_log_left(self) -> None:
        try:
            logs_tabs = self.query_one("#logs-tabs", TabbedContent)
            active_tab = logs_tabs.active
            panel = self.query_one(f"#{active_tab.replace('tab', 'panel')}", RichLog)
            panel.scroll_left(animate=False)
        except Exception: pass

    def action_scroll_log_right(self) -> None:
        try:
            logs_tabs = self.query_one("#logs-tabs", TabbedContent)
            active_tab = logs_tabs.active
            panel = self.query_one(f"#{active_tab.replace('tab', 'panel')}", RichLog)
            panel.scroll_right(animate=False)
        except Exception: pass

    def action_cursor_down(self) -> None:
        try:
            if isinstance(self.focused, ListView): self.focused.action_cursor_down()
        except Exception: pass

    def action_cursor_up(self) -> None:
        try:
            if isinstance(self.focused, ListView): self.focused.action_cursor_up()
        except Exception: pass

    def on_key(self, event) -> None:
        """Handle key presses for custom navigation and view toggles"""
        key = event.key
        pods_list = self.query_one("#pods-list", ListView)
        containers_list = self.query_one("#containers-list", ListView)

        # FIX ULTRA-CRITIQUE : Intercepter la touche Alumet immédiatement, 
        # peu importe quel composant (onglets, listes, etc.) possède le focus actif.
        if key in ["a", "A"]:
            self.action_open_alumet()
            event.prevent_default()
            event.stop()
            return

        # Navigation standard quand le panneau des pods a le focus
        if self.focused == pods_list:
            if key in ["left", "right", "h", "l"]:
                if len(containers_list) > 0:
                    if key in ["right", "l"]: containers_list.action_cursor_down()
                    else: containers_list.action_cursor_up()
                    event.prevent_default()
                    event.stop()
                    return
            elif key == "space":
                self.action_toggle_container()
                event.prevent_default()
                event.stop()
                return

        # Défilement horizontal des logs standards
        try:
            logs_tabs = self.query_one("#logs-tabs", TabbedContent)
            if self.focused == logs_tabs or (self.focused and self.focused in logs_tabs.query("*")):
                if key in ["left", "right", "h", "l"]:
                    if key in ["left", "h"]: self.action_scroll_log_left()
                    else: self.action_scroll_log_right()
                    event.prevent_default()
                    event.stop()
                    return
        except Exception: pass

    def action_toggle_container(self) -> None:
        containers_list = self.query_one("#containers-list", ListView)
        if containers_list.highlighted_child and isinstance(containers_list.highlighted_child, ContainerItem):
            item = containers_list.highlighted_child
            container_name = item.container_name
            if container_name in self.active_containers: self.active_containers.discard(container_name)
            else: self.active_containers.add(container_name)
            item.update_active_state(container_name in self.active_containers)
            self.show_pod_logs()

    def action_toggle_follow(self) -> None:
        self.following_logs = not self.following_logs
        self.update_logs_title()
        if self.following_logs:
            if self._log_follow_timer is None: self._log_follow_timer = self.set_interval(2.0, self._refresh_logs)
        else:
            if self._log_follow_timer is not None:
                self._log_follow_timer.stop()
                self._log_follow_timer = None

    def _refresh_logs(self) -> None:
        if self.following_logs and self.selected_pod: self.show_pod_logs()
    
    def action_open_alumet(self) -> None: 
        """Active ou désactive l'affichage du moniteur Alumet intégré."""
        self.show_alumet = not self.show_alumet

    def action_open_shell(self) -> None:
        if not self.selected_pod: return
        containers = self.k8s_client.get_container_names(self.selected_pod)
        if not containers: return

        containers_list = self.query_one("#containers-list", ListView)
        if self.focused == containers_list and containers_list.highlighted_child and isinstance(containers_list.highlighted_child, ContainerItem):
            container = containers_list.highlighted_child.container_name
        else:
            container = containers[0]

        namespace = self.k8s_client.get_current_namespace()
        pod_name = self.selected_pod.metadata.name

        with self.suspend():
            separator = "─" * 60
            print(f"\033[36m{separator}\033[0m")
            print(f"\033[36m→ \033[1;37mEntering Shell\033[0m")
            print(f"  \033[2mNamespace:\033[0m \033[33m{namespace}\033[0m")
            print(f"  \033[2mPod:\033[0m \033[32m{pod_name}\033[0m")
            print(f"  \033[2mContainer:\033[0m \033[35m{container}\033[0m")
            print(f"\033[36m{separator}\033[0m\n")

            for shell in ["/bin/bash", "/bin/sh", "/bin/ash"]:
                try:
                    result = subprocess.run(["kubectl", "exec", "-it", "-n", namespace, pod_name, "-c", container, "--", shell])
                    if result.returncode == 0: break
                except Exception: continue

            print(f"\n\033[36m{separator}\033[0m")
            print(f"\033[36m← \033[1;37mExited Shell\033[0m")
            print(f"\033[2mPress \033[0m\033[1;32mEnter\033[0m\033[2m to return to \033[0m\033[1;36mlazyk8s\033[0m\033[2m...\033[0m")
            print(f"\033[36m{separator}\033[0m")
            input()

    def action_delete_pod(self) -> None:
        if not self.selected_pod: return
        pod_name = self.selected_pod.metadata.name
        namespace = self.k8s_client.get_current_namespace()

        def handle_confirmation(confirmed: bool) -> None:
            if confirmed:
                success = self.k8s_client.delete_pod(pod_name)
                if success:
                    self.selected_pod = None
                    self.selected_container = None
                    self.refresh_pods()
                    self.set_timer(1.0, self.refresh_pods)
                    self.set_timer(3.0, self.refresh_pods)

        self.push_screen(ConfirmDialog(f"Delete pod [b]{pod_name}[/b] in namespace [b]{namespace}[/b]?\n\nThis action cannot be undone.", title="Confirm Pod Deletion"), handle_confirmation)


class Gui:
    """GUI wrapper class"""

    def __init__(self, k8s_client: K8sClient, app_config: AppConfig):
        self.k8s_client = k8s_client
        self.app_config = app_config
        self.app = LazyK8sApp(k8s_client, app_config)

    def run(self) -> None:
        """Run the GUI application"""
        self.app.run()
