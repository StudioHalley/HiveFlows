#!/usr/bin/env python3
"""HiveFlows CLI — chat with an agent from the terminal."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(help="HiveFlows CLI — chat with your agents.")
workspace_app = typer.Typer(help="Manage workspaces.")
app_cmd = typer.Typer(help="Manage apps in your workspace.")
app.add_typer(workspace_app, name="workspace")
app.add_typer(app_cmd, name="app")

console = Console()

SESSION_DIR = Path.home() / ".hiveflows"
SESSION_FILE = SESSION_DIR / "session.json"

DEFAULT_CORE_URL = "http://localhost:8002"


# ── Session helpers ──────────────────────────────────────────────────────────


def load_session() -> dict:
    if SESSION_FILE.exists():
        return json.loads(SESSION_FILE.read_text())
    return {}


def save_session(data: dict):
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_session()
    existing.update(data)
    SESSION_FILE.write_text(json.dumps(existing, indent=2))


def get_core_url() -> str:
    return os.environ.get("HIVEFLOWS_CORE_URL", DEFAULT_CORE_URL)


# ── Auth helpers ─────────────────────────────────────────────────────────────


def _try_refresh(core_url: str, session: dict) -> dict | None:
    """Attempt to refresh the access token. Returns updated session or None."""
    refresh_token = session.get("refresh_token")
    if not refresh_token:
        return None
    try:
        resp = httpx.post(
            f"{core_url}/auth/refresh",
            json={"refresh_token": refresh_token},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            session["access_token"] = data["access_token"]
            session["token_expires_at"] = time.time() + data.get("expires_in", 3600)
            save_session(session)
            return session
    except httpx.HTTPError:
        pass
    return None


def get_auth_headers() -> dict[str, str]:
    """Build auth headers from session (JWT or API key). Auto-refreshes JWT if needed."""
    session = load_session()
    core_url = get_core_url()

    # JWT auth
    access_token = session.get("access_token")
    if access_token:
        # Check expiry — refresh if within 60s of expiring
        expires_at = session.get("token_expires_at", 0)
        if time.time() > expires_at - 60:
            refreshed = _try_refresh(core_url, session)
            if refreshed:
                session = refreshed
                access_token = session["access_token"]
            else:
                console.print("[red]Session expired. Please run:[/] [bold]hiveflows login[/]")
                raise typer.Exit(1)

        headers = {"Authorization": f"Bearer {access_token}"}
        ws_id = session.get("workspace_id")
        if ws_id:
            headers["X-Workspace-Id"] = ws_id
        return headers

    # Legacy API key (env var or session)
    api_key = session.get("api_key") or os.environ.get("HIVEFLOWS_API_KEY", "")
    if api_key:
        headers = {"X-API-Key": api_key}
        ws_id = session.get("workspace_id")
        if ws_id:
            headers["X-Workspace-Id"] = ws_id
        return headers

    console.print("[red]Not authenticated.[/] Run [bold]hiveflows login[/] first.")
    raise typer.Exit(1)


# ── Login command ────────────────────────────────────────────────────────────


@app.command()
def login(
    api_key: str = typer.Option(None, "--api-key", "-k", help="Login with an API key instead of email/password"),
):
    """Authenticate with HiveFlows (email/password or API key)."""
    core_url = get_core_url()

    if api_key:
        # API key login — validate by calling /agents or /health
        with console.status("Validating API key..."):
            resp = httpx.get(
                f"{core_url}/agents",
                headers={"X-API-Key": api_key},
                timeout=15,
            )
        if resp.status_code == 200:
            save_session({"api_key": api_key, "auth_mode": "api_key"})
            console.print("[green]Authenticated with API key.[/]")
            # Show agents to confirm workspace context
            agents = resp.json()
            if agents:
                console.print(f"[dim]Agents available: {', '.join(a['id'] for a in agents[:5])}[/]")
        else:
            console.print(f"[red]Invalid API key.[/] Server returned {resp.status_code}")
            raise typer.Exit(1)
        return

    # Email/password login
    email = console.input("[bold]Email:[/] ").strip()
    if not email:
        console.print("[red]Email required.[/]")
        raise typer.Exit(1)

    import getpass
    password = getpass.getpass("Password: ")
    if not password:
        console.print("[red]Password required.[/]")
        raise typer.Exit(1)

    with console.status("Logging in..."):
        resp = httpx.post(
            f"{core_url}/auth/login",
            json={"email": email, "password": password},
            timeout=15,
        )

    if resp.status_code != 200:
        detail = resp.json().get("detail", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        console.print(f"[red]Login failed:[/] {detail}")
        raise typer.Exit(1)

    data = resp.json()
    session_data = {
        "auth_mode": "jwt",
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "token_expires_at": time.time() + data.get("expires_in", 3600),
    }
    # Remove legacy api_key if switching to JWT
    session = load_session()
    session.pop("api_key", None)
    session.update(session_data)
    save_session(session)

    console.print(f"[green]Logged in as[/] [bold]{email}[/]")

    # Fetch workspaces and auto-select first one
    try:
        ws_resp = httpx.get(
            f"{core_url}/workspaces",
            headers={"Authorization": f"Bearer {data['access_token']}"},
            timeout=15,
        )
        if ws_resp.status_code == 200:
            workspaces = ws_resp.json()
            if workspaces:
                ws = workspaces[0]
                save_session({
                    "workspace_id": ws["id"],
                    "workspace_slug": ws["slug"],
                })
                console.print(f"[dim]Workspace: {ws['name']} ({ws['slug']}) — {ws.get('role', 'member')}[/]")
                if len(workspaces) > 1:
                    console.print(f"[dim]{len(workspaces)} workspaces available. Use [bold]hiveflows workspace list[/bold] to see all.[/]")
    except httpx.HTTPError:
        pass


# ── Logout command ───────────────────────────────────────────────────────────


@app.command()
def logout():
    """Clear the local session."""
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
    console.print("[dim]Session cleared.[/]")


# ── Workspace commands ───────────────────────────────────────────────────────


@workspace_app.command("list")
def workspace_list():
    """List your workspaces."""
    core_url = get_core_url()
    headers = get_auth_headers()

    with console.status("Fetching workspaces..."):
        resp = httpx.get(f"{core_url}/workspaces", headers=headers, timeout=15)

    if resp.status_code != 200:
        console.print(f"[red]Error {resp.status_code}:[/] {resp.text}")
        raise typer.Exit(1)

    workspaces = resp.json()
    if not workspaces:
        console.print("[dim]No workspaces found.[/]")
        return

    session = load_session()
    active_slug = session.get("workspace_slug", "")

    table = Table(title="Workspaces", border_style="cyan")
    table.add_column("", width=2)
    table.add_column("Slug", style="cyan")
    table.add_column("Name")
    table.add_column("Role", style="dim")

    for ws in workspaces:
        marker = "[green]>[/]" if ws["slug"] == active_slug else " "
        table.add_row(marker, ws["slug"], ws["name"], ws.get("role", ""))

    console.print(table)
    if active_slug:
        console.print(f"\n[dim]Active workspace: [bold]{active_slug}[/bold]. Use [bold]hiveflows workspace use <slug>[/bold] to switch.[/]")


@workspace_app.command("use")
def workspace_use(slug: str = typer.Argument(..., help="Workspace slug to switch to")):
    """Switch active workspace."""
    core_url = get_core_url()
    headers = get_auth_headers()

    with console.status("Switching workspace..."):
        resp = httpx.get(f"{core_url}/workspaces", headers=headers, timeout=15)

    if resp.status_code != 200:
        console.print(f"[red]Error {resp.status_code}:[/] {resp.text}")
        raise typer.Exit(1)

    workspaces = resp.json()
    match = next((ws for ws in workspaces if ws["slug"] == slug), None)

    if not match:
        console.print(f"[red]Workspace '{slug}' not found.[/]")
        available = ", ".join(ws["slug"] for ws in workspaces)
        if available:
            console.print(f"[dim]Available: {available}[/]")
        raise typer.Exit(1)

    save_session({
        "workspace_id": match["id"],
        "workspace_slug": match["slug"],
        "conversation_id": None,  # Reset conversation on workspace switch
    })
    console.print(f"[green]Switched to[/] [bold]{match['name']}[/] ({match['slug']}) — {match.get('role', 'member')}")


# ── Status command ───────────────────────────────────────────────────────────


@app.command()
def status():
    """Show current session info."""
    session = load_session()
    if not session:
        console.print("[dim]Not logged in. Run [bold]hiveflows login[/bold].[/]")
        return

    auth_mode = session.get("auth_mode", "unknown")
    ws_slug = session.get("workspace_slug", "none")
    conv_id = session.get("conversation_id", "")
    agent_id = session.get("agent_id", "")

    console.print(f"[bold]Auth:[/] {auth_mode}")
    console.print(f"[bold]Workspace:[/] {ws_slug}")
    if conv_id:
        console.print(f"[bold]Conversation:[/] {conv_id[:12]}...")
    if agent_id:
        console.print(f"[bold]Agent:[/] {agent_id}")

    if auth_mode == "jwt":
        expires_at = session.get("token_expires_at", 0)
        remaining = int(expires_at - time.time())
        if remaining > 0:
            console.print(f"[bold]Token expires in:[/] {remaining // 60}m {remaining % 60}s")
        else:
            console.print("[yellow]Token expired — will auto-refresh on next request.[/]")


# ── Chat helpers ─────────────────────────────────────────────────────────────


def send_resume(
    core_url: str,
    headers: dict,
    conversation_id: str,
    agent_id: str,
    decisions: list[dict],
) -> dict:
    """POST decisions to /chat/{conversation_id}/resume after a HITL interrupt."""
    payload = {"agent_id": agent_id, "decisions": decisions}
    with console.status("Resuming..."):
        resp = httpx.post(
            f"{core_url}/chat/{conversation_id}/resume",
            json=payload,
            headers=headers,
            timeout=300,
        )
    if resp.status_code != 200:
        console.print(f"[red]Resume error {resp.status_code}:[/] {resp.text}")
        raise typer.Exit(1)
    return resp.json()


def send_message(core_url: str, headers: dict, message: str, agent_id: str, conversation_id: str | None) -> dict:
    payload = {"agent_id": agent_id, "message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id

    with console.status("Thinking..."):
        resp = httpx.post(
            f"{core_url}/chat",
            json=payload,
            headers=headers,
            timeout=300,
        )

    if resp.status_code != 200:
        console.print(f"[red]Error {resp.status_code}:[/] {resp.text}")
        raise typer.Exit(1)

    return resp.json()


def stream_message(core_url: str, headers: dict, message: str, agent_id: str, conversation_id: str | None) -> dict:
    """Stream a response from /chat/stream and display tokens in real-time."""
    payload = {"agent_id": agent_id, "message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id

    conv_id = conversation_id or ""
    tokens: list[str] = []
    current_event: str | None = None
    interrupt_data: dict | None = None

    try:
        with httpx.stream(
            "POST",
            f"{core_url}/chat/stream",
            json=payload,
            headers=headers,
            timeout=300,
        ) as response:
            if response.status_code != 200:
                console.print(f"[red]Error {response.status_code}[/]")
                raise typer.Exit(1)

            console.print("[dim]Agent >[/] ", end="")

            for line in response.iter_lines():
                if line.startswith("event: "):
                    current_event = line[7:]
                elif line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue

                    if current_event == "message_start":
                        conv_id = data.get("conversation_id", conv_id)

                    elif current_event == "token":
                        content = data.get("content", "")
                        console.print(content, end="", highlight=False)
                        tokens.append(content)

                    elif current_event == "tool_call":
                        name = data.get("name", "?")
                        args = data.get("args", {})
                        console.print(f"\n  [dim]tool:[/] [cyan]{name}[/]({json.dumps(args, ensure_ascii=False)[:80]})", end="")

                    elif current_event == "tool_result":
                        output = data.get("output", "")
                        console.print(f"\n  [dim]> {str(output)[:120]}[/]", end="")
                        console.print(f"\n[dim]Agent >[/] ", end="")

                    elif current_event == "message_end":
                        console.print()

                    elif current_event == "interrupt":
                        console.print()
                        interrupt_data = data
                        break

                    elif current_event == "error":
                        console.print(f"\n[red]Stream error: {data.get('message')}[/]")

    except httpx.ReadTimeout:
        console.print("\n[red]Stream timeout.[/]")
        raise typer.Exit(1)

    # Handle HITL interrupt
    if interrupt_data:
        action_requests = interrupt_data.get("action_requests", [])
        review_configs = interrupt_data.get("review_configs", [])

        console.print("\n[bold yellow]Approval required[/bold yellow]")

        decisions = []
        for req in action_requests:
            name = req.get("name", "?")
            args = req.get("args", {})
            console.print(f"\n  Tool : [cyan]{name}[/]")
            console.print(f"  Args : {json.dumps(args, ensure_ascii=False)[:200]}")

            allowed = ["approve", "reject"]
            for rc in review_configs:
                if rc.get("action_name") == name:
                    allowed = rc.get("allowed_decisions", ["approve", "reject"])
                    break

            choice = ""
            while choice not in allowed:
                choice = console.input(f"  [bold][{'/'.join(allowed)}]:[/] ").strip().lower()
            decisions.append({"type": choice})

        resume_resp = send_resume(core_url, headers, conv_id, agent_id, decisions)
        display_response(resume_resp)
        return {"conversation_id": conv_id, "response": resume_resp.get("response", "")}

    return {"conversation_id": conv_id, "response": "".join(tokens)}


def display_response(data: dict):
    response = data.get("response", "")
    tool_calls = data.get("tool_calls", [])
    conv_id = data.get("conversation_id", "")

    if tool_calls:
        for tc in tool_calls:
            name = tc.get("name", "?")
            args = tc.get("args", {})
            console.print(f"  [dim]tool:[/] [cyan]{name}[/]({json.dumps(args, ensure_ascii=False)[:80]})")
        console.print()

    console.print(Panel(Markdown(response), border_style="green", title="Agent"))
    console.print(f"[dim]conversation: {conv_id}[/]")


# ── Chat command ─────────────────────────────────────────────────────────────


@app.command()
def chat(
    message: str = typer.Argument(None, help="Message to send (omit for interactive mode)"),
    agent: str = typer.Option("assistant-principal", "--agent", "-a", help="Agent ID"),
    conversation_id: str = typer.Option(None, "--conversation-id", "-c", help="Continue a conversation"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Interactive REPL mode"),
    new: bool = typer.Option(False, "--new", "-n", help="Start a new conversation (ignore saved session)"),
    stream: bool = typer.Option(False, "--stream", "-s", help="Stream response via SSE (real-time tokens)"),
):
    """Send a message to a HiveFlows agent."""
    core_url = get_core_url()
    headers = get_auth_headers()

    # Load saved conversation if not explicitly new
    session = load_session()
    if not new and not conversation_id and session.get("conversation_id"):
        conversation_id = session["conversation_id"]
        console.print(f"[dim]Resuming conversation {conversation_id[:8]}...[/]")

    ws_slug = session.get("workspace_slug")
    if ws_slug:
        console.print(f"[dim]Workspace: {ws_slug}[/]")

    def _send(msg: str, conv_id: str | None) -> dict:
        if stream:
            return stream_message(core_url, headers, msg, agent, conv_id)
        return send_message(core_url, headers, msg, agent, conv_id)

    if interactive or message is None:
        # REPL mode
        mode_label = "streaming" if stream else "standard"
        console.print(f"[bold]HiveFlows Interactive Mode[/] [dim]({mode_label})[/]")
        console.print("[dim]Type 'exit' or Ctrl+C to quit. '/new' to start a new conversation.[/]\n")

        while True:
            try:
                user_input = console.input("[bold blue]You>[/] ")
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Bye![/]")
                break

            user_input = user_input.strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                console.print("[dim]Bye![/]")
                break
            if user_input == "/new":
                conversation_id = None
                console.print("[dim]New conversation started.[/]")
                continue

            data = _send(user_input, conversation_id)
            conversation_id = data.get("conversation_id")
            save_session({"conversation_id": conversation_id, "agent_id": agent})
            if not stream:
                display_response(data)
            else:
                console.print(f"[dim]conversation: {conversation_id}[/]")
            console.print()
    else:
        # Single message mode
        data = _send(message, conversation_id)
        conversation_id = data.get("conversation_id")
        save_session({"conversation_id": conversation_id, "agent_id": agent})
        if not stream:
            display_response(data)
        else:
            console.print(f"[dim]conversation: {conversation_id}[/]")


# ── App helpers ──────────────────────────────────────────────────────────────


def _get_workspace_id() -> str:
    session = load_session()
    ws_id = session.get("workspace_id")
    if not ws_id:
        console.print("[red]No active workspace.[/] Run [bold]hiveflows workspace use <slug>[/].")
        raise typer.Exit(1)
    return ws_id


# ── App commands ─────────────────────────────────────────────────────────────


@app_cmd.command("catalog")
def app_catalog():
    """List all available apps in the catalog."""
    core_url = get_core_url()
    headers = get_auth_headers()

    with console.status("Fetching catalog..."):
        resp = httpx.get(f"{core_url}/apps/catalog", headers=headers, timeout=15)

    if resp.status_code != 200:
        console.print(f"[red]Error {resp.status_code}:[/] {resp.text}")
        raise typer.Exit(1)

    apps = resp.json()
    if not apps:
        console.print("[dim]Catalog is empty.[/]")
        return

    table = Table(title="App Catalog", border_style="cyan")
    table.add_column("Slug", style="cyan")
    table.add_column("Name")
    table.add_column("Version", style="dim")
    table.add_column("Type", style="dim")
    table.add_column("Description")

    for a in apps:
        table.add_row(
            a["slug"],
            a["name"],
            a.get("version", ""),
            a.get("app_type", ""),
            (a.get("description") or "")[:60],
        )

    console.print(table)


@app_cmd.command("list")
def app_list():
    """List installed apps in the active workspace."""
    core_url = get_core_url()
    headers = get_auth_headers()
    ws_id = _get_workspace_id()

    with console.status("Fetching apps..."):
        resp = httpx.get(f"{core_url}/w/{ws_id}/apps", headers=headers, timeout=15)

    if resp.status_code != 200:
        console.print(f"[red]Error {resp.status_code}:[/] {resp.text}")
        raise typer.Exit(1)

    apps = resp.json()
    if not apps:
        console.print("[dim]No apps installed in this workspace.[/]")
        return

    status_color = {
        "running": "green",
        "stopped": "yellow",
        "pulling": "blue",
        "error": "red",
    }

    table = Table(title="Installed Apps", border_style="cyan")
    table.add_column("Slug", style="cyan")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Container", style="dim")
    table.add_column("IP", style="dim")

    for a in apps:
        s = a.get("status", "unknown")
        color = status_color.get(s, "white")
        table.add_row(
            a["app_slug"],
            a["app_name"],
            f"[{color}]{s}[/]",
            a.get("container_name", ""),
            a.get("container_ip") or "—",
        )

    console.print(table)


@app_cmd.command("install")
def app_install(
    slug: str = typer.Argument(..., help="App slug to install"),
    env: list[str] = typer.Option([], "--env", "-e", help="Env vars KEY=VALUE (repeatable)"),
):
    """Install an app in the active workspace."""
    core_url = get_core_url()
    headers = get_auth_headers()
    ws_id = _get_workspace_id()

    env_values: dict = {}
    for kv in env:
        if "=" not in kv:
            console.print(f"[red]Invalid env var format:[/] {kv!r} (expected KEY=VALUE)")
            raise typer.Exit(1)
        k, v = kv.split("=", 1)
        env_values[k] = v

    payload: dict = {"app_slug": slug}
    if env_values:
        payload["env_values"] = env_values

    with console.status(f"Installing [cyan]{slug}[/]..."):
        resp = httpx.post(
            f"{core_url}/w/{ws_id}/apps/install",
            json=payload,
            headers=headers,
            timeout=30,
        )

    if resp.status_code == 409:
        console.print(f"[yellow]App [bold]{slug}[/bold] is already installed.[/]")
        return
    if resp.status_code not in (200, 201, 202):
        console.print(f"[red]Error {resp.status_code}:[/] {resp.text}")
        raise typer.Exit(1)

    data = resp.json()
    console.print(f"[green]Installing[/] [bold]{slug}[/] — status: [cyan]{data.get('status')}[/]")
    console.print(f"[dim]Container: {data.get('container_name')} | instance: {data.get('instance_id', '')[:8]}...[/]")
    console.print("[dim]Follow progress with: hiveflows app status " + slug + "[/]")


@app_cmd.command("start")
def app_start(slug: str = typer.Argument(..., help="App slug")):
    """Start a stopped app."""
    core_url = get_core_url()
    headers = get_auth_headers()
    ws_id = _get_workspace_id()

    with console.status(f"Starting [cyan]{slug}[/]..."):
        resp = httpx.post(f"{core_url}/w/{ws_id}/apps/{slug}/start", headers=headers, timeout=120)

    if resp.status_code != 200:
        console.print(f"[red]Error {resp.status_code}:[/] {resp.text}")
        raise typer.Exit(1)

    console.print(f"[green]Started[/] [bold]{slug}[/]")


@app_cmd.command("stop")
def app_stop(slug: str = typer.Argument(..., help="App slug")):
    """Stop a running app."""
    core_url = get_core_url()
    headers = get_auth_headers()
    ws_id = _get_workspace_id()

    with console.status(f"Stopping [cyan]{slug}[/]..."):
        resp = httpx.post(f"{core_url}/w/{ws_id}/apps/{slug}/stop", headers=headers, timeout=30)

    if resp.status_code != 200:
        console.print(f"[red]Error {resp.status_code}:[/] {resp.text}")
        raise typer.Exit(1)

    console.print(f"[yellow]Stopped[/] [bold]{slug}[/]")


@app_cmd.command("restart")
def app_restart(slug: str = typer.Argument(..., help="App slug")):
    """Restart an app."""
    core_url = get_core_url()
    headers = get_auth_headers()
    ws_id = _get_workspace_id()

    with console.status(f"Restarting [cyan]{slug}[/]..."):
        resp = httpx.post(f"{core_url}/w/{ws_id}/apps/{slug}/restart", headers=headers, timeout=120)

    if resp.status_code != 200:
        console.print(f"[red]Error {resp.status_code}:[/] {resp.text}")
        raise typer.Exit(1)

    console.print(f"[green]Restarted[/] [bold]{slug}[/]")


@app_cmd.command("logs")
def app_logs(
    slug: str = typer.Argument(..., help="App slug"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(100, "--lines", "-n", help="Number of past lines to show"),
):
    """Stream logs for an app."""
    core_url = get_core_url()
    headers = get_auth_headers()
    ws_id = _get_workspace_id()

    url = f"{core_url}/w/{ws_id}/apps/{slug}/logs?lines={lines}&follow={str(follow).lower()}"

    console.print(f"[dim]Logs for [bold]{slug}[/bold] (lines={lines}, follow={follow}) — Ctrl+C to stop[/]\n")

    try:
        with httpx.stream("GET", url, headers=headers, timeout=None) as response:
            if response.status_code != 200:
                body = response.read()
                console.print(f"[red]Error {response.status_code}:[/] {body.decode()}")
                raise typer.Exit(1)

            for line in response.iter_lines():
                if not line:
                    continue
                # Strip SSE framing
                if line.startswith("data: "):
                    text = line[6:]
                    try:
                        payload = json.loads(text)
                        console.print(payload.get("log", text))
                    except json.JSONDecodeError:
                        console.print(text)
                elif line.startswith("event: error"):
                    pass  # next line will be data:
                elif not line.startswith(":"):
                    console.print(line)

    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/]")


@app_cmd.command("env")
def app_env(
    slug: str = typer.Argument(..., help="App slug"),
    kv: list[str] = typer.Argument(None, help="KEY=VALUE pairs to set"),
    list_keys: bool = typer.Option(False, "--list", "-l", help="List env keys (values are encrypted)"),
):
    """Get or set environment variables for an app.

    Examples:\n
      hiveflows app env myapp --list\n
      hiveflows app env myapp PASSWORD=secret PORT=8080
    """
    core_url = get_core_url()
    headers = get_auth_headers()
    ws_id = _get_workspace_id()

    if list_keys or not kv:
        # Fetch app status to show that env is configured
        resp = httpx.get(f"{core_url}/w/{ws_id}/apps", headers=headers, timeout=15)
        if resp.status_code != 200:
            console.print(f"[red]Error {resp.status_code}:[/] {resp.text}")
            raise typer.Exit(1)
        apps = resp.json()
        match = next((a for a in apps if a["app_slug"] == slug), None)
        if not match:
            console.print(f"[red]App [bold]{slug}[/bold] not installed in this workspace.[/]")
            raise typer.Exit(1)
        console.print(f"[bold]{slug}[/] env: [dim](values are encrypted at rest)[/]")
        return

    env_values: dict = {}
    for pair in kv:
        if "=" not in pair:
            console.print(f"[red]Invalid format:[/] {pair!r} (expected KEY=VALUE)")
            raise typer.Exit(1)
        k, v = pair.split("=", 1)
        env_values[k] = v

    with console.status(f"Updating env for [cyan]{slug}[/]..."):
        resp = httpx.put(
            f"{core_url}/w/{ws_id}/apps/{slug}/env",
            json={"env_values": env_values},
            headers=headers,
            timeout=15,
        )

    if resp.status_code != 200:
        console.print(f"[red]Error {resp.status_code}:[/] {resp.text}")
        raise typer.Exit(1)

    data = resp.json()
    keys = data.get("keys", list(env_values.keys()))
    console.print(f"[green]Env updated[/] for [bold]{slug}[/]: {', '.join(keys)}")
    console.print("[dim]Restart the app to apply: hiveflows app restart " + slug + "[/]")


@app_cmd.command("status")
def app_status(slug: str = typer.Argument(..., help="App slug")):
    """Show detailed status of an installed app."""
    core_url = get_core_url()
    headers = get_auth_headers()
    ws_id = _get_workspace_id()

    with console.status(f"Fetching status for [cyan]{slug}[/]..."):
        resp = httpx.get(f"{core_url}/w/{ws_id}/apps", headers=headers, timeout=15)

    if resp.status_code != 200:
        console.print(f"[red]Error {resp.status_code}:[/] {resp.text}")
        raise typer.Exit(1)

    apps = resp.json()
    match = next((a for a in apps if a["app_slug"] == slug), None)
    if not match:
        console.print(f"[red]App [bold]{slug}[/bold] not installed in this workspace.[/]")
        raise typer.Exit(1)

    status_color = {"running": "green", "stopped": "yellow", "pulling": "blue", "error": "red"}
    s = match.get("status", "unknown")
    color = status_color.get(s, "white")

    table = Table(show_header=False, border_style="dim", min_width=40)
    table.add_column("Key", style="bold", width=18)
    table.add_column("Value")

    table.add_row("App", match["app_name"])
    table.add_row("Slug", match["app_slug"])
    table.add_row("Status", f"[{color}]{s}[/]")
    table.add_row("Container", match.get("container_name", ""))
    table.add_row("IP", match.get("container_ip") or "—")
    table.add_row("Image", match.get("app_image", ""))
    if match.get("started_at"):
        table.add_row("Started at", match["started_at"])

    console.print(table)


@app_cmd.command("uninstall")
def app_uninstall(
    slug: str = typer.Argument(..., help="App slug"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Uninstall an app from the active workspace."""
    if not yes:
        confirm = console.input(f"[bold yellow]Uninstall [red]{slug}[/red]? This will stop and remove the container. [y/N]:[/] ")
        if confirm.strip().lower() != "y":
            console.print("[dim]Cancelled.[/]")
            return

    core_url = get_core_url()
    headers = get_auth_headers()
    ws_id = _get_workspace_id()

    with console.status(f"Uninstalling [cyan]{slug}[/]..."):
        resp = httpx.delete(f"{core_url}/w/{ws_id}/apps/{slug}", headers=headers, timeout=30)

    if resp.status_code == 404:
        console.print(f"[yellow]App [bold]{slug}[/bold] not found in this workspace.[/]")
        return
    if resp.status_code != 200:
        console.print(f"[red]Error {resp.status_code}:[/] {resp.text}")
        raise typer.Exit(1)

    console.print(f"[green]Uninstalled[/] [bold]{slug}[/]")


if __name__ == "__main__":
    app()
