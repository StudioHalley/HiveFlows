# portal-client

HiveFlows Portal client — exposes local HTTP services through a persistent WebSocket tunnel to your HiveFlows instance.

## Quick start

### 1. Install

Download the binary for your platform from the releases page, or build from source:

```bash
cd clients/portal
./build.sh
# binary at dist/portal-client-<os>-<arch>
sudo cp dist/portal-client-linux-amd64 /usr/local/bin/portal-client
sudo chmod +x /usr/local/bin/portal-client
```

### 2. Enroll

On your HiveFlows dashboard, go to **Workspace → Portal Devices → Add Device**. Copy the `portal-client init` command displayed and run it on your device:

```bash
portal-client init \
  --server wss://hiveflows.example.com/portal/ws \
  --token <enroll_token> \
  --device-slug nas-maison \
  --workspace acme
```

This creates `~/.config/portal-client/portal.yaml` (Linux/macOS) or `%APPDATA%\portal-client\portal.yaml` (Windows).

### 3. Configure services

Edit the generated config to add the local services you want to expose:

```yaml
services:
  - name: homeassistant
    route: http://192.168.1.20:8123
  - name: vlc
    route: http://127.0.0.1:8004
```

Only services listed here are forwarded. Any `REQUEST` for an unlisted service is rejected immediately — no local connection is made.

### 4. Run

```bash
portal-client run
```

The client connects to the portal server, sends a HELLO, and waits for HTTP forwarding requests. It reconnects automatically with exponential backoff (1 s → 2 s → … → 60 s) on disconnection.

## Run as a service

### Linux (systemd)

```bash
sudo useradd -r -s /bin/false portal
sudo cp examples/portal-client.service /etc/systemd/system/
# Edit ExecStart path if needed
sudo systemctl daemon-reload
sudo systemctl enable --now portal-client
sudo journalctl -u portal-client -f
```

### macOS (launchd)

```bash
cp examples/com.hiveflows.portal-client.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.hiveflows.portal-client.plist
tail -f /tmp/portal-client.log
```

### Windows (NSSM)

Download [NSSM](https://nssm.cc/), then:

```powershell
nssm install portal-client "C:\Program Files\portal-client\portal-client.exe" run
nssm set portal-client AppStdout "C:\Logs\portal-client.log"
nssm set portal-client AppStderr "C:\Logs\portal-client.log"
nssm start portal-client
```

Or with `sc.exe`:

```powershell
sc.exe create portal-client binPath= "\"C:\Program Files\portal-client\portal-client.exe\" run" start= auto
sc.exe start portal-client
```

## Configuration reference

| Field | Description |
|---|---|
| `server_url` | WebSocket URL (`wss://…/portal/ws`) |
| `workspace` | Workspace slug (filled by `init`) |
| `device_slug` | Device identifier (filled by `init`) |
| `token` | Permanent device token — **keep secret** |
| `services` | List of `{name, route}` entries (allowlist) |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `enrollment failed: enroll failed (HTTP 400): Enroll token already used` | Token was already consumed | Create a new device on the dashboard |
| `enrollment failed: enroll failed (HTTP 400): Enroll token has expired` | Token TTL (15 min) expired | Create a new device on the dashboard |
| `load config: read config … no such file` | Config not written yet | Run `portal-client init` first |
| Device shows "disconnected" on dashboard | Connection dropped | Check network; client reconnects automatically |
| `service "x" is not in the allowlist` | Service not in portal.yaml | Add it under `services:` |
