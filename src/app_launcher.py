#!/usr/bin/env python3
import sys
import os
import subprocess

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..")))

from src.config import ConfigManager
from src.brain import get_verdict
from src.network_guard import sync_hosts_file

def launch_claude_desktop():
    config = ConfigManager()

    if not config.protection_enabled:
        open_real_app(config)
        return

    if not config.allowed_ips:
        show_mac_alert(
            "ClaudeGuard Protection 🚫",
            "Access to Claude Desktop blocked.\n\nReason: No allowed VPN IPs configured in whitelist.\n\nUse 'claudeguard add-ip <IP>' to configure."
        )
        sys.exit(1)

    # Fail closed — only "allowed" launches; "blocked"/"offline" both refuse.
    verdict, ip, err = get_verdict(config, timeout=2.0)

    if verdict != "allowed":
        if verdict == "offline":
            show_mac_alert(
                "ClaudeGuard Protection 🚫",
                "Access to Claude Desktop BLOCKED.\n\nNo internet connection — your IP can't be verified.\n\nExecution cancelled to prevent region ban."
            )
        else:
            ip_str = ip if ip else "Unknown"
            err_msg = f"\nError: {err}" if err else ""
            show_mac_alert(
                "ClaudeGuard Protection 🚫",
                f"Access to Claude Desktop BLOCKED.\n\nCurrent Public IP: {ip_str}\nStatus: NOT IN ALLOWED VPN WHITELIST{err_msg}\n\nExecution cancelled to prevent region ban."
            )
        sys.exit(1)

    # IP is valid! Unblock domain and start app
    sync_hosts_file(block_claude_domains=False, block_update_domains=config.block_auto_updates, config=config)
    open_real_app(config)

def open_real_app(config):
    app_path = config.real_claude_app
    if os.path.exists(app_path):
        subprocess.run(["open", "-a", app_path], check=False)
    else:
        show_mac_alert("ClaudeGuard Error", f"Claude Desktop app not found at '{app_path}'.")

def show_mac_alert(title, message):
    script = f'display alert "{title}" message "{message}" as critical'
    subprocess.run(["osascript", "-e", script], check=False)

if __name__ == "__main__":
    launch_claude_desktop()
