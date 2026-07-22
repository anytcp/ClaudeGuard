<div align="center">

# 🛡️ ClaudeGuard

**A VPN-whitelist guard for Claude on macOS.**

Blocks the `claude.ai` site, the **Claude Desktop** app and the **Claude Code CLI** (`claude`)
whenever your public IP is not on your VPN whitelist — so you never touch Claude from the wrong region by accident.

**English** · [Русский](README.ru.md)

</div>

---

## Why

If your account is region-sensitive, a single request from a non-VPN IP can get you flagged.
ClaudeGuard makes that mistake impossible: every launch and every running session is checked against your
whitelist of VPN IPs, and access is cut the instant you fall off it.

## Features

- 🔐 **IP whitelist** — Claude opens **only** when your public IP matches one of your VPN nodes.
- ⏱ **Instant pre-flight check** — launching `claude` or `Claude.app` verifies the public IP in < 0.5s (STUN over UDP). Not whitelisted → blocked before anything connects.
- 🧠 **One brain, one verdict** — a background daemon publishes the decision to a state file; the CLI, the app launcher and the menu bar all read the same verdict, so nothing can disagree.
- 🚀 **Autostart at login** via `LaunchAgent`.
- 🟢 **Menu bar UI** — live status (🟢 protected / 🔴 blocked / ⚪ offline / 🟡 off), toggles, one-click whitelist.
- ❄️ **Freeze auto-updates** — blocks Claude's update servers and locks the update dirs.

## Install

The installer compiles the native binaries (Swift + C) **on your machine**. Locally-compiled binaries
carry no quarantine flag, so **Gatekeeper never blocks them** — no Apple Developer account, no code-signing,
no notarization, no Homebrew. The only requirement is Xcode Command Line Tools (the installer offers to
install them if missing).

**Get the folder, then run:**

```bash
./install.sh
```

It compiles for your architecture (Apple Silicon or Intel), installs the menu bar daemon at login,
wraps the `claude` command, and asks for your password once (`sudo` is needed to edit `/etc/hosts` and pf).
At the end it offers to whitelist your current IP — accept **only if you are on your VPN right now.**

<details>
<summary>Optional: one-line install from GitHub</summary>

The same `install.sh` can fetch and build the source itself:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ivblz/ClaudeGuard/main/install.sh)"
```

Running locally from the folder never touches GitHub.
</details>

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
| `claudeguard launch-desktop` | Launch Claude Desktop with a pre-flight check |
| `claudeguard start` / `stop` | Start / stop the menu bar daemon |
| `claudeguard set-cli-path <path>` | Point at the real `claude` binary if auto-detect misses it |

## How it works

1. **STUN-first IP detection** — the public IP is resolved via a single UDP STUN round-trip (tens of ms), falling back to HTTP echo services on networks that block UDP.
2. **The brain** (`src/brain.py`) turns that IP into one verdict — `allowed` / `blocked` / `offline` — and it **fails closed**: only a confirmed `allowed` lets Claude run.
3. **Single source of truth** — the menu bar daemon writes its verdict to `~/.config/claudeguard/state.json` on every check. The CLI wrapper and app launcher read it (instant), and fall back to their own check only if the daemon is down.
4. **Enforcement** — domains are blocked via `/etc/hosts` + a pf firewall rule (TCP + UDP/QUIC); the desktop app is force-quit with an alert; a running `claude` session is killed the moment the IP leaves the whitelist.

> **Scope:** this is accidental-leak protection for your own machine, not a defense against a malicious admin. The truly unbypassable layer (a Network Extension content filter) requires a paid Apple Developer account; the on-device approach here is the free ceiling and covers accidental leaks well.

## Project structure

```
src/
  brain.py          The single decision core (reads the daemon verdict; STUN fallback)
  ip_checker.py     Public-IP detection: STUN over UDP, then HTTP
  config.py         Config at ~/.config/claudeguard/config.json
  network_guard.py  /etc/hosts + pf firewall block/unblock
  update_guard.py   Freeze/unfreeze Claude auto-updates
  cli_wrapper.py    Pre-flight interceptor for the `claude` command
  app_launcher.py   Pre-flight interceptor for Claude.app
  main.swift        Native menu bar daemon (AppKit): monitoring, alerts, state file
  hosts_helper.c    Privileged helper (via sudo, not setuid) that writes /etc/hosts + pf
bin/claudeguard     Management CLI
assets/AppIcon.png  App icon
install.sh          Compiles natively on-device and installs everything
uninstall.sh        Clean removal, restores the original `claude`
```

## License

MIT — see [LICENSE](LICENSE).
