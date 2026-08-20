<div align="center">

# ClaudeGuard

**A VPN-whitelist guard for Claude on Linux.**

Blocks the **Claude Desktop** app (Electron), the **Claude Code CLI** (`claude`) and every Claude/Anthropic domain (`claude.ai`, `claude.com`, `anthropic.com` and all their subdomains) whenever your public IP is not on your VPN whitelist - so you never touch Claude from the wrong region by accident.

**English** · [Русский](README.ru.md)

</div>

---

## Why

If your account is region-sensitive, a single request from the wrong IP can get your account flagged.
ClaudeGuard solves that: every launch and every running session is checked against your whitelist of allowed IPs, and access is cut the instant you fall off it.

## Features

- **IP whitelist** - Claude opens **only** when your public IP matches one of your VPN nodes.
- **Instant pre-flight check** - launching `claude` verifies the public IP in < 0.5s (STUN over UDP). Not whitelisted = blocked before anything connects.
- **One brain, one verdict** - a background daemon publishes the decision to a state file; the CLI and the app launcher read the same verdict, so nothing can disagree.
- **Autostart at login** via systemd user service.
- **Two daemon modes** - headless (systemd, for servers) or system tray icon (for desktops with X11/Wayland via pystray).
- **Freeze auto-updates** - blocks Claude's update servers and locks the update dirs.
- **Default model override** - pin a model for every `claude` session (e.g. `claude-opus-4-8`); the CLI wrapper injects `--model` automatically.
- **Claude Desktop guard** - detects and kills Electron-based Claude Desktop when blocked.

## Install

The installer compiles the C root helper **on your machine** with gcc/clang.
Python 3 and iptables are installed automatically if missing (pacman/apt/dnf/zypper/nix-env).

Two ways to install - both give the exact same result.

**Option 1 - one line** (downloads and builds from GitHub):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/anytcp/ClaudeGuard/main/install.sh)"
```

**Option 2 - from a local copy** (a cloned or downloaded repo):

```bash
./install.sh
```

The installer will:
1. Compile the C root helper with gcc.
2. Ask whether you want **system tray mode** (requires pystray + Pillow) or **headless mode** (pure systemd). If no display server is detected, headless is chosen automatically.
3. Create a systemd user service for the daemon and a systemd path unit for the root helper.
4. Hook the `claude` command so every invocation goes through ClaudeGuard first.
5. Ask for your password once (`sudo` is needed for `/etc/hosts` and iptables).
6. Offer to whitelist your current IP - accept **only if you are on your VPN right now.**

**Supported distros:** Arch, Debian/Ubuntu, Fedora, openSUSE, NixOS - anything with systemd and a C compiler.

**Uninstall:**

```bash
./uninstall.sh
```

## CLI

After install the `claudeguard` command is available:

| Command | What it does |
|---|---|
| `claudeguard status` | Current status, IP and whitelist |
| `claudeguard add-ip <ip>` | Add an IP or CIDR to the whitelist |
| `claudeguard remove-ip <ip>` | Remove an IP from the whitelist |
| `claudeguard list-ips` | List whitelisted IPs |
| `claudeguard enable-protection` / `disable-protection` | Turn protection on / off |
| `claudeguard block-updates` / `allow-updates` | Lock / unlock auto-updates |
| `claudeguard set-model <model>` | Set the default model (e.g. `claude-opus-4-8`) |
| `claudeguard enable-model` / `disable-model` | Turn the model override on / off |
| `claudeguard launch-desktop` | Launch Claude Desktop with a pre-flight check |
| `claudeguard start` / `stop` | Start / stop the daemon (via systemd) |
| `claudeguard doctor` | Check every hook is still attached, and re-attach it |
| `claudeguard set-cli-path <path>` | Point at the real `claude` binary if auto-detect misses it |

### Surviving Claude reinstalls

Reinstalling Claude Code puts the real `claude` binary back over ClaudeGuard's shim,
and updating it deletes the version-stamped path the shim hands off to - so the guard
would silently detach while the daemon still reports "allowed".

ClaudeGuard therefore discovers those locations instead of hard-coding them, and
re-attaches whatever came loose: the daemon self-heals **every 60s**, the `claude`
wrapper re-hooks on each run, and `/etc/hosts` is re-applied whenever something
external edits it. Run `claudeguard doctor` to see the state of every hook (and fix
it immediately) at any time.

## How it works

1. **STUN-first IP detection** - the public IP is resolved via a single UDP STUN round-trip (tens of ms), falling back to HTTP echo services on networks that block UDP.
2. **The brain** (`src/brain.py`) turns that IP into one verdict - `allowed` / `blocked` / `offline` - and it **fails closed**: only a confirmed `allowed` lets Claude run.
3. **Single source of truth** - the daemon writes its verdict to `~/.config/claudeguard/state.json` on every check. The CLI wrapper and launcher read it (instant), and fall back to their own check only if the daemon is down.
4. **Enforcement** - the whole Claude/Anthropic domain family (~175 hosts across 5 apex domains) is blocked via `/etc/hosts` + iptables rules (TCP + UDP/QUIC); Claude Desktop (Electron) is killed; a running `claude` session is terminated the moment the IP leaves the whitelist.
5. **Root helper** - a systemd path unit watches a trigger file; when the user daemon touches it, a root service applies the staged `/etc/hosts` and iptables rules. No sudoers, no setuid.

> **Scope:** this is accidental-leak protection for your own machine, not a defense against a malicious admin.

## Project structure

```
src/
  brain.py          Decision core (reads daemon verdict; STUN fallback)
  ip_checker.py     Public IP detection: STUN over UDP, then HTTP
  config.py         Config at ~/.config/claudeguard/config.json
  network_guard.py  Block/unblock via /etc/hosts + iptables
  update_guard.py   Freeze/unfreeze Claude auto-updates
  integrity.py      Discovers real Claude paths; re-hooks after reinstall
  cli_wrapper.py    Pre-flight interceptor for the `claude` command
  app_launcher.py   Pre-flight interceptor for Claude Desktop
  daemon.py         Background daemon (systemd): monitoring, enforcement, state file
  tray.py           Optional system tray icon (pystray + Pillow)
  hosts_helper.c    Root helper (systemd): writes /etc/hosts + iptables
  helper.py         Dispatcher for daemon -> helper calls
bin/claudeguard     Management CLI
install.sh          Compiles natively and installs everything
uninstall.sh        Clean removal, restores the original `claude`
```

## License

MIT - see [LICENSE](LICENSE).
