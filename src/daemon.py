#!/usr/bin/env python3
"""ClaudeGuard Linux daemon — background service that enforces VPN-only access
to Claude.

Replaces the macOS Swift menu bar daemon (main.swift). Runs as a systemd user
service, or standalone. Optionally shows a system tray icon when started with
--tray and pystray + Pillow are available.

Fail-closed: only a confirmed "allowed" lets Claude run. Both "blocked" and
"offline" block domains AND kill running Claude processes.
"""
import glob
import json
import os
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..")))

from src.config import ConfigManager
from src.ip_checker import fetch_public_ip, is_ip_allowed
from src.network_guard import sync_hosts_file
from src.update_guard import apply_auto_update_lock
from src.integrity import repair, DESKTOP_PROCESS_PATTERNS

STATE_PATH = os.path.expanduser("~/.config/claudeguard/state.json")
CHECK_INTERVAL = 3.0
INTEGRITY_INTERVAL = 60.0


class GuardDaemon:
    def __init__(self, tray_mode=False):
        self.tray_mode = tray_mode
        self.state = "offline"
        self.current_ip = "..."
        self.protection_enabled = True
        self.block_auto_updates = True
        self.model_override_enabled = False
        self.default_model = "claude-opus-4-8"
        self.allowed_ips = []

        self.config = ConfigManager()

        self._check_lock = threading.Lock()
        self._check_running = False
        self._recheck_queued = False

        self._last_applied_domains_blocked = None
        self._last_applied_block_updates = None
        self._has_alerted_this_block = False

        self._has_network = True

        self.running = True
        self.tray = None

    # -- config ---------------------------------------------------------------

    def load_config(self):
        self.config = ConfigManager()
        self.protection_enabled = self.config.protection_enabled
        self.block_auto_updates = self.config.block_auto_updates
        self.model_override_enabled = self.config.model_override_enabled
        self.default_model = self.config.default_model
        self.allowed_ips = self.config.allowed_ips

    # -- state file -----------------------------------------------------------

    def write_state_file(self):
        state_dir = os.path.dirname(STATE_PATH)
        os.makedirs(state_dir, exist_ok=True)
        d = {
            "state": self.state,
            "ip": self.current_ip,
            "protection_enabled": self.protection_enabled,
            "updated_at": time.time(),
        }
        tmp = STATE_PATH + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(d, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, STATE_PATH)
        except Exception:
            pass

    # -- drift detection ------------------------------------------------------

    def hosts_drifted(self):
        if self._last_applied_domains_blocked is None:
            return False
        try:
            with open("/etc/hosts", "r") as f:
                content = f.read()
            has_domains = "# BEGIN CLAUDEGUARD BLOCKS" in content
            has_updates = "# BEGIN CLAUDEGUARD UPDATE BLOCKS" in content
            return (has_domains != self._last_applied_domains_blocked or
                    has_updates != self._last_applied_block_updates)
        except Exception:
            return False

    # -- network connectivity hint (like NWPathMonitor on macOS) ---------------

    def _check_network_interfaces(self):
        """Quick check: is any non-loopback interface up and carrier-on?
        Equivalent to macOS NWPathMonitor.status == .satisfied."""
        try:
            for iface in os.listdir("/sys/class/net"):
                if iface == "lo":
                    continue
                operstate_path = f"/sys/class/net/{iface}/operstate"
                try:
                    with open(operstate_path) as f:
                        state = f.read().strip()
                    if state == "up":
                        return True
                except (OSError, IOError):
                    continue
        except OSError:
            pass
        return False

    # -- process control -------------------------------------------------------

    def _kill_claude_desktop(self):
        """Kill Claude Desktop (Electron) on Linux."""
        killed = False
        for pattern in DESKTOP_PROCESS_PATTERNS:
            try:
                r = subprocess.run(["pgrep", "-f", pattern],
                                   capture_output=True, text=True)
                if r.returncode == 0:
                    subprocess.run(["pkill", "-15", "-f", pattern], check=False)
                    time.sleep(0.15)
                    subprocess.run(["pkill", "-9", "-f", pattern], check=False)
                    killed = True
            except Exception:
                pass
        return killed

    def _kill_claude_cli_sessions(self):
        """Kill all running `claude` CLI processes. Belt-and-suspenders: the CLI
        wrapper monitors its own child, but sessions started without the wrapper
        (or before the shim was installed) escape that. This catches them."""
        killed = False
        my_pid = os.getpid()
        try:
            r = subprocess.run(["pgrep", "-f", "claude"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                return False
            for pid_str in r.stdout.strip().split("\n"):
                pid_str = pid_str.strip()
                if not pid_str:
                    continue
                try:
                    pid = int(pid_str)
                except ValueError:
                    continue
                if pid == my_pid:
                    continue
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        cmdline = f.read().decode("utf-8", errors="replace")
                except (OSError, IOError):
                    continue
                if any(x in cmdline for x in [
                    "daemon.py", "claudeguard", "ClaudeGuard",
                    "helper.py", "cli_wrapper.py", "tray.py",
                    "integrity.py", "app_launcher.py",
                    "ip\x00monitor", "pgrep", "pkill",
                ]):
                    continue
                if "claude" in cmdline.lower():
                    try:
                        os.kill(pid, signal.SIGTERM)
                        killed = True
                    except OSError:
                        pass
        except Exception:
            pass
        if killed:
            time.sleep(0.3)
            # SIGKILL stragglers
            try:
                r = subprocess.run(["pgrep", "-f", "claude"],
                                   capture_output=True, text=True)
                if r.returncode == 0:
                    for pid_str in r.stdout.strip().split("\n"):
                        pid_str = pid_str.strip()
                        if not pid_str:
                            continue
                        try:
                            pid = int(pid_str)
                        except ValueError:
                            continue
                        if pid == my_pid:
                            continue
                        try:
                            with open(f"/proc/{pid}/cmdline", "rb") as f:
                                cmdline = f.read().decode("utf-8", errors="replace")
                            if any(x in cmdline for x in [
                                "daemon.py", "claudeguard", "ClaudeGuard",
                                "helper.py", "cli_wrapper.py",
                            ]):
                                continue
                            if "claude" in cmdline.lower():
                                os.kill(pid, signal.SIGKILL)
                        except (OSError, IOError):
                            pass
            except Exception:
                pass
        return killed

    def _show_notification(self, title, message):
        for cmd in [
            ["notify-send", "--urgency=critical", "--app-name=ClaudeGuard",
             title, message],
            ["zenity", "--error", f"--title={title}", f"--text={message}"],
            ["kdialog", "--error", message, "--title", title],
        ]:
            try:
                subprocess.run(cmd, check=True, timeout=5,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                continue

    # -- IP resolution --------------------------------------------------------

    def _resolve_public_ip(self):
        """Resolve the public IP. Uses a short timeout when the network appears
        down (like macOS NWPathMonitor hint) and retries when it's up."""
        if self._has_network:
            for attempt in range(2):
                ip, _ = fetch_public_ip(timeout=2.0)
                if ip:
                    return ip
                if attempt < 1:
                    time.sleep(0.5)
        else:
            ip, _ = fetch_public_ip(timeout=1.0)
            if ip:
                self._has_network = True
                return ip
        return None

    # -- core check -----------------------------------------------------------

    def perform_check(self):
        self.load_config()

        self._has_network = self._check_network_interfaces()

        if not self.protection_enabled:
            new_state = "disabled"
        else:
            ip = self._resolve_public_ip()
            if ip:
                self.current_ip = ip
                new_state = "allowed" if is_ip_allowed(ip, self.allowed_ips) else "blocked"
            else:
                self.current_ip = "No Internet"
                new_state = "offline"

        previous = self.state
        self.state = new_state
        self._apply(previous, new_state)
        self.write_state_file()

        if self.tray:
            try:
                self.tray.update_state(new_state, self.current_ip)
            except Exception:
                pass

    def _apply(self, previous, new):
        if self.hosts_drifted():
            self._last_applied_domains_blocked = None
            self._last_applied_block_updates = None

        # Fail closed: both blocked AND offline block domains.
        want_blocked = new in ("blocked", "offline")
        domains_changed = (self._last_applied_domains_blocked != want_blocked)
        updates_changed = (self._last_applied_block_updates != self.block_auto_updates)

        if domains_changed or updates_changed:
            self._last_applied_domains_blocked = want_blocked
            sync_hosts_file(
                block_claude_domains=want_blocked,
                block_update_domains=self.block_auto_updates,
                config=self.config,
                skip_dns=(new == "offline"),
            )

        if updates_changed:
            self._last_applied_block_updates = self.block_auto_updates
            apply_auto_update_lock(self.block_auto_updates)

        # Kill ALL Claude processes on blocked OR offline. Fail closed.
        # macOS relies on pf/hosts blocking to cut connections; on Linux we
        # kill explicitly as well for defense in depth.
        if new in ("blocked", "offline"):
            killed_desktop = self._kill_claude_desktop()
            killed_cli = self._kill_claude_cli_sessions()
            killed = killed_desktop or killed_cli

            if killed and not self._has_alerted_this_block:
                self._has_alerted_this_block = True
                if new == "blocked":
                    self._show_notification(
                        "ClaudeGuard 🚫 Blocked",
                        f"All Claude sessions were closed.\n"
                        f"Your public IP ({self.current_ip}) is not in your "
                        f"allowed VPN whitelist.",
                    )
                else:
                    self._show_notification(
                        "ClaudeGuard 🚫 Offline",
                        "All Claude sessions were closed.\n"
                        "No internet connection — your IP can't be verified.",
                    )
        else:
            self._has_alerted_this_block = False

    # -- coalescing recheck ---------------------------------------------------

    def recheck(self):
        with self._check_lock:
            if self._check_running:
                self._recheck_queued = True
                return
            self._check_running = True

        while True:
            with self._check_lock:
                self._recheck_queued = False

            self.perform_check()

            with self._check_lock:
                if self._recheck_queued:
                    continue
                self._check_running = False
                break

    # -- background loops -----------------------------------------------------

    def _check_loop(self):
        while self.running:
            self.recheck()
            time.sleep(CHECK_INTERVAL)

    def _integrity_loop(self):
        time.sleep(2.0)
        while self.running:
            try:
                report = repair(self.config)
                for change in report["changed"]:
                    print(f"[ClaudeGuard] repaired: {change}", flush=True)
            except Exception as e:
                print(f"[ClaudeGuard] integrity error: {e}", flush=True)
            time.sleep(INTEGRITY_INTERVAL)

    def _network_monitor(self):
        """Watch for network changes using `ip monitor` on Linux.
        Monitors both route and link events for instant detection."""
        try:
            proc = subprocess.Popen(
                ["ip", "monitor", "route", "link"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
            while self.running:
                line = proc.stdout.readline()
                if not line:
                    break
                self.recheck()
            proc.terminate()
        except Exception:
            pass

    def _interface_monitor(self):
        """Poll /sys/class/net/*/operstate for instant WiFi down detection.
        Fires recheck when connectivity changes — equivalent to NWPathMonitor."""
        last_up = self._check_network_interfaces()
        while self.running:
            time.sleep(0.5)
            now_up = self._check_network_interfaces()
            if now_up != last_up:
                last_up = now_up
                self.recheck()

    # -- main -----------------------------------------------------------------

    def run(self):
        signal.signal(signal.SIGTERM, lambda s, f: self.stop())
        signal.signal(signal.SIGINT, lambda s, f: self.stop())

        print("[ClaudeGuard] daemon starting...", flush=True)

        threads = [
            threading.Thread(target=self._check_loop, daemon=True, name="check"),
            threading.Thread(target=self._integrity_loop, daemon=True, name="integrity"),
            threading.Thread(target=self._network_monitor, daemon=True, name="netmon"),
            threading.Thread(target=self._interface_monitor, daemon=True, name="ifmon"),
        ]
        for t in threads:
            t.start()

        if self.tray_mode:
            try:
                from src.tray import TrayIcon
                self.tray = TrayIcon(self)
                print("[ClaudeGuard] system tray mode", flush=True)
                self.tray.run()
            except ImportError:
                print("[ClaudeGuard] pystray not available, running headless",
                      flush=True)
                self._wait()
        else:
            print("[ClaudeGuard] headless mode (systemd)", flush=True)
            self._wait()

    def _wait(self):
        try:
            while self.running:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            pass

    def stop(self):
        print("[ClaudeGuard] daemon stopping...", flush=True)
        self.running = False
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass


def main():
    tray_mode = "--tray" in sys.argv
    daemon = GuardDaemon(tray_mode=tray_mode)
    daemon.run()


if __name__ == "__main__":
    main()
