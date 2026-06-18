#!/usr/bin/env python3
"""
StackChan Device Status TUI
════════════════════════════════════════════════════════════════════════

Beautiful terminal dashboard for monitoring StackChan robot devices in
real-time using the StackChan REST API and WebSocket protocol.

SETUP
─────
    cd tools/tui
    pip install -r requirements.txt

USAGE
─────
    # Supply a pre-obtained JWT token:
    python device_status.py --server http://localhost:12800 --token <jwt>

    # Auto-login with credentials:
    python device_status.py --server http://localhost:12800 \\
        --username user@example.com --password mypassword

    # Watch a single device (highlight it in the table):
    python device_status.py --server http://localhost:12800 \\
        --token <jwt> --mac AA:BB:CC:DD:EE:FF

    # Custom refresh interval (seconds):
    python device_status.py --server http://localhost:12800 \\
        --token <jwt> --refresh 10

CONTROLS
────────
    q / Ctrl+C   Quit the dashboard
    r            Force an immediate data refresh

API NOTES
─────────
    The dashboard uses the StackChan v2 REST API:
      • POST /stackChan/v2/user/login    – obtain JWT
      • GET  /stackChan/v2/devices       – list bound devices (JWT header: token)

    Hardware telemetry (WiFi RSSI, battery level, volume, brightness) is NOT
    currently exposed through the StackChan server REST API. Those metrics
    display "N/A" but the visual bars are ready for future API extensions.

    Online/offline status is tracked in real-time via the WebSocket endpoint:
      ws://<host>/stackChan/ws?deviceType=App&deviceId=<uuid>
    Messages 0x16 (DeviceOffline) and 0x17 (DeviceOnline) update device state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# ── dependency guards ─────────────────────────────────────────────────────────
try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed.  Run:  pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    from rich import box
    from rich.align import Align
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("ERROR: rich not installed.  Run:  pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False

# ── constants ─────────────────────────────────────────────────────────────────
DEFAULT_SERVER   = "http://localhost:12800"
DEFAULT_REFRESH  = 5
WS_PING_INTERVAL = 10
MAX_LOG_ENTRIES  = 12

# WebSocket binary message types
MSG_TEXT_MESSAGE  = 0x07
MSG_PING          = 0x10
MSG_PONG          = 0x11
MSG_DEVICE_OFFLINE = 0x16
MSG_DEVICE_ONLINE  = 0x17

# Rich markup palette
C_CYAN   = "bold cyan"
C_GREEN  = "bold green"
C_YELLOW = "bold yellow"
C_RED    = "bold red"
C_DIM    = "dim"
C_WHITE  = "bold white"

LOGO = """\
[bold cyan]  ╔══════════════════════════════════════════════════════════════╗[/]
[bold cyan]  ║[/]  [bold white]╭──────╮[/]                                                  [bold cyan]║[/]
[bold cyan]  ║[/]  [bold white]│[/] [yellow]◉[/]  [yellow]◉[/] [bold white]│[/]  [bold cyan]S T A C K C H A N[/]   [dim]Device Status Dashboard[/]   [bold cyan]║[/]
[bold cyan]  ║[/]  [bold white]│[/]  [white]────[/]  [bold white]│[/]  [dim]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]   [bold cyan]║[/]
[bold cyan]  ║[/]  [bold white]│[/]  [white]╰──╯[/]  [bold white]│[/]  [dim]Real-time monitoring  ·  REST API + WebSocket[/]   [bold cyan]║[/]
[bold cyan]  ║[/]  [bold white]╰──────╯[/]                                                  [bold cyan]║[/]
[bold cyan]  ╚══════════════════════════════════════════════════════════════╝[/]"""


# ── data model ────────────────────────────────────────────────────────────────
@dataclass
class DeviceRecord:
    mac: str
    name: str
    uid: int
    bind_time: str
    online: Optional[bool] = None   # None = unknown, True = online, False = offline


@dataclass
class AppState:
    server_url: str
    token: Optional[str]
    mac_filter: Optional[str]
    refresh_interval: int

    # runtime state
    devices: List[DeviceRecord]          = field(default_factory=list)
    server_ok: bool                      = False
    auth_ok: bool                        = False
    ws_connected: bool                   = False
    last_refresh: Optional[datetime]     = None
    error: Optional[str]                 = None
    is_fetching: bool                    = False
    next_refresh_at: float               = 0.0
    log_entries: List[str]               = field(default_factory=list)


# ── HTTP helpers ──────────────────────────────────────────────────────────────
async def login(server: str, username: str, password: str) -> Optional[str]:
    """POST /stackChan/v2/user/login and return JWT or None."""
    url = f"{server.rstrip('/')}/stackChan/v2/user/login"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={"username": username, "password": password})
            body = resp.json()
            if body.get("code") == 0:
                return body["data"]["token"]
    except Exception:
        pass
    return None


async def fetch_devices(server: str, token: str) -> tuple[bool, bool, List[DeviceRecord], Optional[str]]:
    """GET /stackChan/v2/devices and parse the response.

    Returns (server_ok, auth_ok, devices, error_msg).
    """
    url = f"{server.rstrip('/')}/stackChan/v2/devices"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"token": token})
            body = resp.json()
            if body.get("code") == 0:
                raw = body.get("data") or []
                devices = [
                    DeviceRecord(
                        mac=d.get("mac", ""),
                        name=d.get("name", ""),
                        uid=d.get("uid", 0),
                        bind_time=d.get("bind_time") or d.get("bindTime") or "",
                    )
                    for d in raw
                ]
                return True, True, devices, None
            code = body.get("code", -1)
            if code in (401, 403, 10002):
                return True, False, [], f"Auth error (code {code})"
            return True, True, [], f"API error (code {code}): {body.get('message','')}"
    except httpx.ConnectError:
        return False, False, [], "Cannot reach server — check --server URL"
    except httpx.TimeoutException:
        return False, False, [], "Request timed out"
    except Exception as exc:
        return False, False, [], f"Unexpected error: {exc}"


# ── WebSocket worker ──────────────────────────────────────────────────────────
def ws_worker(state: AppState, lock: threading.Lock, stop_event: threading.Event) -> None:
    """Background thread that maintains a WebSocket connection and
    updates device online/offline status via 0x16/0x17 messages."""

    if not WS_AVAILABLE:
        _log(state, lock, "[yellow]⚠[/]  websockets not installed — no real-time updates")
        return

    asyncio.run(_ws_loop(state, lock, stop_event))


async def _ws_loop(state: AppState, lock: threading.Lock, stop_event: threading.Event) -> None:
    device_id = str(uuid.uuid4())
    server = state.server_url
    ws_url = (
        server.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
        + f"/stackChan/ws?deviceType=App&deviceId={device_id}"
    )

    while not stop_event.is_set():
        try:
            async with websockets.connect(ws_url, ping_interval=None, close_timeout=5) as ws:
                with lock:
                    state.ws_connected = True
                    _log(state, lock, "[green]⚡[/]  WebSocket connected")

                async def send_ping() -> None:
                    while not stop_event.is_set():
                        await asyncio.sleep(WS_PING_INTERVAL)
                        if not stop_event.is_set():
                            pkt = struct.pack(">BI", MSG_PING, 0)
                            await ws.send(pkt)

                ping_task = asyncio.create_task(send_ping())
                try:
                    async for raw in ws:
                        if stop_event.is_set():
                            break
                        _handle_ws_message(raw, state, lock)
                finally:
                    ping_task.cancel()

        except (ConnectionClosed, OSError, Exception) as exc:
            with lock:
                state.ws_connected = False
                _log(state, lock, f"[red]✗[/]  WebSocket disconnected: {type(exc).__name__}")

        if not stop_event.is_set():
            await asyncio.sleep(5)   # reconnect after 5 s

    with lock:
        state.ws_connected = False


def _handle_ws_message(raw: bytes | str, state: AppState, lock: threading.Lock) -> None:
    if isinstance(raw, str):
        return  # ignore text frames
    if len(raw) < 5:
        return

    msg_type = raw[0]
    payload = raw[5:]

    if msg_type == MSG_DEVICE_ONLINE:
        # payload is the device name string
        name = payload.decode("utf-8", errors="replace").strip()
        with lock:
            for dev in state.devices:
                if dev.name == name or not name:
                    dev.online = True
            _log(state, lock, f"[green]▲[/]  Device online: [bold]{name or '(unknown)'}[/]")

    elif msg_type == MSG_DEVICE_OFFLINE:
        name = payload.decode("utf-8", errors="replace").strip()
        with lock:
            for dev in state.devices:
                if dev.name == name or not name:
                    dev.online = False
            _log(state, lock, f"[red]▼[/]  Device offline: [bold]{name or '(unknown)'}[/]")

    elif msg_type == MSG_UPDATE_NAME:
        with lock:
            _log(state, lock, "[cyan]✏[/]  Device name updated")


MSG_UPDATE_NAME = 0x0D   # define after function refs it


def _log(state: AppState, lock: threading.Lock, msg: str) -> None:
    """Append a timestamped entry to the activity log (call with lock held)."""
    ts = datetime.now().strftime("%H:%M:%S")
    state.log_entries.append(f"[dim]{ts}[/]  {msg}")
    if len(state.log_entries) > MAX_LOG_ENTRIES:
        state.log_entries = state.log_entries[-MAX_LOG_ENTRIES:]


# ── Rich layout builders ──────────────────────────────────────────────────────
def _bar(value: Optional[float], width: int = 20, *, low: float = 20, high: float = 60) -> Text:
    """Return a colored progress bar as a Rich Text object."""
    if value is None:
        return Text("─" * width + "  N/A", style="dim")

    filled = max(0, min(width, int(value / 100 * width)))
    bar = "█" * filled + "░" * (width - filled)
    pct = f"  {int(value):>3}%"

    if value < low:
        style = "bold red"
    elif value < high:
        style = "bold yellow"
    else:
        style = "bold green"

    t = Text()
    t.append(bar, style=style)
    t.append(pct, style="dim")
    return t


def _signal_bar(rssi: Optional[int], width: int = 10) -> Text:
    """RSSI (dBm, negative) → colored bar.  None → N/A."""
    if rssi is None:
        return Text("─" * width + "  N/A", style="dim")

    # typical range: −30 (excellent) to −90 (unusable)
    clamped = max(-90, min(-30, rssi))
    frac = (clamped + 90) / 60        # 0.0 … 1.0
    filled = max(1, int(frac * width))
    bar = "▊" * filled + "░" * (width - filled)
    label = f"  {rssi} dBm"

    if rssi >= -60:
        style = "bold green"
    elif rssi >= -75:
        style = "bold yellow"
    else:
        style = "bold red"

    t = Text()
    t.append(bar, style=style)
    t.append(label, style="dim")
    return t


def _online_badge(status: Optional[bool]) -> Text:
    if status is True:
        return Text("● Online",  style="bold green")
    if status is False:
        return Text("○ Offline", style="bold red")
    return Text("◌ Unknown", style="bold yellow")


def _fmt_bind_time(raw: str, short: bool = False) -> str:
    """Try to parse bind_time and return a formatted string.

    short=True returns a compact "May 10 '26" form for table columns.
    """
    if not raw:
        return "—"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            if short:
                return dt.strftime("%b %d '%y")
            return dt.strftime("%b %d, %Y  %H:%M")
        except ValueError:
            pass
    return raw


# ── panel factories ───────────────────────────────────────────────────────────
def make_header() -> Panel:
    return Panel(
        Align.center(Text.from_markup(LOGO)),
        border_style="bright_black",
        padding=(0, 0),
    )


def make_server_panel(state: AppState) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", width=10)
    grid.add_column()

    # Connection
    if state.server_ok:
        conn = Text("● Connected", style="bold green")
    elif state.error:
        conn = Text("○ Unreachable", style="bold red")
    else:
        conn = Text("◌ Connecting…", style="bold yellow")

    grid.add_row("Status",  conn)
    grid.add_row("URL",     Text(state.server_url, style="cyan"))

    auth_text = (
        Text("✓ JWT active",    style="bold green")   if state.auth_ok else
        Text("✗ No auth",       style="bold yellow")  if not state.token else
        Text("✗ Token invalid", style="bold red")
    )
    ws_text = (
        Text("⚡ Live",   style="bold green") if state.ws_connected else
        Text("○ Polling", style="dim yellow")
    )
    grid.add_row("Auth",    auth_text)
    grid.add_row("WSocket", ws_text)
    grid.add_row("Devices", Text(str(len(state.devices)), style="bold white"))

    if state.error:
        grid.add_row("", Text(""))
        grid.add_row("Error", Text(state.error, style="bold red"))

    return Panel(
        grid,
        title="[bold cyan]⬡  Server[/]",
        border_style="cyan",
        padding=(1, 2),
    )


def make_device_panel(state: AppState) -> Panel:
    """Detail panel for the focused/filtered device, or summary when no filter."""

    focused: Optional[DeviceRecord] = None
    if state.mac_filter:
        for d in state.devices:
            if d.mac.upper() == state.mac_filter.upper():
                focused = d
                break

    if focused is None and state.devices:
        # fall back to first device
        focused = state.devices[0]

    if focused is None:
        content: Any = Align.center(
            Text("No devices found", style="dim"),
            vertical="middle",
        )
        return Panel(
            content,
            title="[bold cyan]◈  Device Info[/]",
            border_style="cyan",
            padding=(1, 2),
        )

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", width=12)
    grid.add_column()

    grid.add_row("Name",       Text(focused.name or "—", style="bold white"))
    grid.add_row("MAC",        Text(focused.mac, style="bright_black"))
    grid.add_row("Status",     _online_badge(focused.online))
    grid.add_row("Bound UID",  Text(str(focused.uid) if focused.uid else "Unbound", style="cyan"))
    grid.add_row("Bound since", Text(_fmt_bind_time(focused.bind_time), style="dim"))

    # ── hardware metrics (not yet in API) ──────────────────────────────
    grid.add_row("", Text(""))
    grid.add_row(
        Text("Hardware", style="bold dim"),
        Text("(not exposed via REST API)", style="dim italic"),
    )

    for label, val in [
        ("WiFi RSSI",   None),
        ("Battery",     None),
        ("Volume",      None),
        ("Brightness",  None),
    ]:
        if label == "WiFi RSSI":
            bar = _signal_bar(None)
        else:
            bar = _bar(None)
        grid.add_row(label, bar)

    return Panel(
        grid,
        title=f"[bold cyan]◈  {focused.name or focused.mac}[/]",
        border_style="cyan",
        padding=(1, 2),
    )


def make_devices_table(state: AppState) -> Panel:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold cyan",
        row_styles=["", "dim"],
        expand=True,
        border_style="bright_black",
    )
    table.add_column("Status",      min_width=11, no_wrap=True)
    table.add_column("Name",        ratio=3)
    table.add_column("MAC Address", width=19,    style="bright_black", no_wrap=True)
    table.add_column("UID",         width=8)
    table.add_column("Bound",       width=12, no_wrap=True)

    if not state.devices:
        table.add_row(
            Text("—", style="dim"),
            Text("No devices" if state.auth_ok else "Login required", style="dim italic"),
            "", "", "",
        )
    else:
        for dev in state.devices:
            is_focused = (
                state.mac_filter and dev.mac.upper() == state.mac_filter.upper()
            )
            name_style = "bold white" if is_focused else "white"
            row_prefix = "▶ " if is_focused else "  "

            table.add_row(
                _online_badge(dev.online),
                Text(row_prefix + (dev.name or "—"), style=name_style),
                dev.mac,
                str(dev.uid) if dev.uid else Text("—", style="dim"),
                _fmt_bind_time(dev.bind_time, short=True),
            )

    return Panel(
        table,
        title=f"[bold cyan]≡  All Devices[/]  [dim]({len(state.devices)} total)[/]",
        border_style="cyan",
        padding=(0, 1),
    )


def make_activity_log(state: AppState) -> Panel:
    lines = state.log_entries[-MAX_LOG_ENTRIES:] if state.log_entries else ["[dim]No events yet…[/]"]
    text = Text.from_markup("\n".join(lines))
    return Panel(
        text,
        title="[bold cyan]⚡  Activity Log[/]",
        border_style="bright_black",
        padding=(0, 2),
    )


def make_footer(state: AppState, fetching: bool) -> Panel:
    last = state.last_refresh.strftime("%H:%M:%S") if state.last_refresh else "─"

    remaining = max(0, int(state.next_refresh_at - time.time()))
    if fetching:
        refresh_info = "[bold yellow]⟳ fetching…[/]"
    else:
        refresh_info = f"[dim]next in[/] [bold cyan]{remaining}s[/]"

    status = Text.from_markup(
        f"[dim]↺ last refresh[/] [cyan]{last}[/]  {refresh_info}"
        f"    [bright_black]│[/]  [dim]q[/] quit   [dim]r[/] refresh   [dim]Ctrl+C[/] exit"
    )

    return Panel(
        Align.left(status),
        border_style="bright_black",
        padding=(0, 2),
    )


# ── layout assembly ────────────────────────────────────────────────────────────
def _build_layout() -> Layout:
    layout = Layout(name="root")
    layout.split_column(
        Layout(name="header",  size=9),
        Layout(name="top",     size=16),
        Layout(name="devices", size=14),
        Layout(name="bottom",  size=10),
        Layout(name="footer",  size=3),
    )
    layout["top"].split_row(
        Layout(name="server", ratio=3),
        Layout(name="device", ratio=5),
    )
    layout["bottom"].split_row(
        Layout(name="log",    ratio=1),
    )
    return layout


def _update_layout(
    layout: Layout,
    state: AppState,
    fetching: bool,
) -> None:
    layout["header"].update(make_header())
    layout["server"].update(make_server_panel(state))
    layout["device"].update(make_device_panel(state))
    layout["devices"].update(make_devices_table(state))
    layout["log"].update(make_activity_log(state))
    layout["footer"].update(make_footer(state, fetching))


# ── main controller ────────────────────────────────────────────────────────────
class StackChanTUI:
    def __init__(
        self,
        server: str,
        token: Optional[str],
        mac_filter: Optional[str],
        refresh: int,
    ) -> None:
        self.state = AppState(
            server_url=server,
            token=token,
            mac_filter=mac_filter,
            refresh_interval=refresh,
        )
        self._lock    = threading.Lock()
        self._stop    = threading.Event()
        self._console = Console()
        self._refresh_flag = threading.Event()

    # ── data fetching ─────────────────────────────────────────────────────────
    def _fetch_once(self) -> None:
        """Synchronous wrapper: run async fetch in a new event loop."""
        asyncio.run(self._async_fetch())

    async def _async_fetch(self) -> None:
        token = self.state.token
        if not token:
            with self._lock:
                self.state.error = "No token — provide --token or --username/--password"
                self.state.server_ok = False
                self.state.auth_ok = False
            return

        ok, auth, devices, err = await fetch_devices(self.state.server_url, token)

        with self._lock:
            self.state.server_ok  = ok
            self.state.auth_ok    = auth
            self.state.error      = err

            if devices:
                # Preserve online status from previous fetch
                old_status: Dict[str, Optional[bool]] = {
                    d.mac: d.online for d in self.state.devices
                }
                for dev in devices:
                    dev.online = old_status.get(dev.mac)
                self.state.devices = devices

                if not self.state.log_entries:
                    _log(self.state, self._lock,
                         f"[cyan]✓[/]  Loaded [bold]{len(devices)}[/] device(s)")

            self.state.last_refresh = datetime.now()
            self.state.next_refresh_at = time.time() + self.state.refresh_interval

    # ── fetch loop thread ─────────────────────────────────────────────────────
    def _fetch_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                self.state.is_fetching = True
            try:
                self._fetch_once()
            finally:
                with self._lock:
                    self.state.is_fetching = False

            # Wait for refresh interval or manual trigger
            self._refresh_flag.wait(timeout=self.state.refresh_interval)
            self._refresh_flag.clear()

    # ── keyboard listener thread ──────────────────────────────────────────────
    def _key_listener(self) -> None:
        """Crude but portable key listener (no dependencies, no tty mode changes)."""
        try:
            import tty, termios, select
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            tty.setraw(fd)
            try:
                while not self._stop.is_set():
                    ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                    if ready:
                        ch = sys.stdin.read(1)
                        if ch in ("q", "Q", "\x03", "\x04"):
                            self._stop.set()
                        elif ch in ("r", "R"):
                            self._refresh_flag.set()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass   # silently skip on unsupported terminals

    # ── main run ──────────────────────────────────────────────────────────────
    def run(self) -> None:
        # Threads
        threads = [
            threading.Thread(target=self._fetch_loop, daemon=True),
            threading.Thread(target=self._key_listener, daemon=True),
        ]
        if WS_AVAILABLE:
            threads.append(
                threading.Thread(
                    target=ws_worker,
                    args=(self.state, self._lock, self._stop),
                    daemon=True,
                )
            )
        else:
            with self._lock:
                _log(self.state, self._lock,
                     "[yellow]⚠[/]  websockets not installed — no real-time WS updates")

        for t in threads:
            t.start()

        layout = _build_layout()

        # initial render before first fetch completes
        with self._lock:
            _update_layout(layout, self.state, fetching=True)

        try:
            with Live(
                layout,
                refresh_per_second=2,
                screen=True,
                console=self._console,
            ):
                while not self._stop.is_set():
                    with self._lock:
                        fetching = self.state.is_fetching
                        _update_layout(layout, self.state, fetching=fetching)
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()


# ── CLI entry point ────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="device_status",
        description="StackChan Device Status TUI — beautiful terminal dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--server", default=DEFAULT_SERVER, metavar="URL",
        help=f"StackChan server base URL (default: {DEFAULT_SERVER})",
    )
    p.add_argument(
        "--token", default=None, metavar="JWT",
        help="Pre-obtained JWT token for v2 API auth",
    )
    p.add_argument(
        "--username", default=None, metavar="USER",
        help="Login username (auto-obtain JWT if --token not provided)",
    )
    p.add_argument(
        "--password", default=None, metavar="PASS",
        help="Login password (used with --username)",
    )
    p.add_argument(
        "--mac", default=None, metavar="MAC",
        help="Device MAC address to highlight/focus (AA:BB:CC:DD:EE:FF)",
    )
    p.add_argument(
        "--refresh", default=DEFAULT_REFRESH, type=int, metavar="SECONDS",
        help=f"Data refresh interval in seconds (default: {DEFAULT_REFRESH})",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    token = args.token

    # Auto-login if credentials supplied
    if not token and args.username and args.password:
        console = Console()
        with console.status("[cyan]Logging in…[/]"):
            token = asyncio.run(login(args.server, args.username, args.password))
        if token:
            console.print(f"[green]✓[/] Login successful — JWT obtained")
        else:
            console.print("[red]✗[/] Login failed — check credentials")
            sys.exit(1)

    tui = StackChanTUI(
        server=args.server,
        token=token,
        mac_filter=args.mac,
        refresh=args.refresh,
    )
    tui.run()


if __name__ == "__main__":
    main()
