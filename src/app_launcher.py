#!/usr/bin/env python3
import sys
import os
import subprocess

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..")))

from src.config import ConfigManager
from src.brain import get_verdict
from src.integrity import discover_desktop_app
from src.network_guard import sync_hosts_file


def launch_claude_desktop():
    config = ConfigManager()

    if not config.protection_enabled:
        open_real_app(config)
        return

    if not config.allowed_ips:
        show_alert(
            "ClaudeGuard Protection 🚫",
            "Access to Claude Desktop blocked.\n\nReason: No allowed VPN IPs configured in whitelist.\n\nUse 'claudeguard add-ip <IP>' to configure."
        )
        sys.exit(1)

    verdict, ip, err = get_verdict(config, timeout=2.0)

    if verdict != "allowed":
        if verdict == "offline":
            show_alert(
                "ClaudeGuard Protection 🚫",
                "Access to Claude Desktop BLOCKED.\n\nNo internet connection — your IP can't be verified.\n\nExecution cancelled to prevent region ban."
            )
        else:
            ip_str = ip if ip else "Unknown"
            err_msg = f"\nError: {err}" if err else ""
            show_alert(
                "ClaudeGuard Protection 🚫",
                f"Access to Claude Desktop BLOCKED.\n\nCurrent Public IP: {ip_str}\nStatus: NOT IN ALLOWED VPN WHITELIST{err_msg}\n\nExecution cancelled to prevent region ban."
            )
        sys.exit(1)

    sync_hosts_file(block_claude_domains=False, block_update_domains=config.block_auto_updates, config=config)
    open_real_app(config)


def open_real_app(config):
    app_path = config.real_claude_app
    if not app_path or not os.path.exists(app_path):
        app_path = discover_desktop_app(config)
        if app_path:
            config.config["real_claude_app_path"] = app_path
            config.save_config()

    if app_path and os.path.exists(app_path):
        if os.path.isfile(app_path) and os.access(app_path, os.X_OK):
            subprocess.Popen([app_path], start_new_session=True)
        elif os.path.isdir(app_path):
            subprocess.Popen(["xdg-open", app_path], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            show_alert("ClaudeGuard Error",
                       "Claude Desktop app not found. Run 'claudeguard doctor'.")
    else:
        show_alert("ClaudeGuard Error",
                   "Claude Desktop app not found. Run 'claudeguard doctor'.")


def show_alert(title, message):
    """Show a dialog on Linux, trying zenity, kdialog, xmessage, or terminal."""
    for cmd in [
        ["zenity", "--error", f"--title={title}", f"--text={message}"],
        ["kdialog", "--error", message, "--title", title],
        ["xmessage", "-center", f"{title}\n\n{message}"],
    ]:
        try:
            subprocess.run(cmd, check=True, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            continue
    print(f"\n{title}\n{'=' * len(title)}\n{message}", file=sys.stderr)


if __name__ == "__main__":
    launch_claude_desktop()
