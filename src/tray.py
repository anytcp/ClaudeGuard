#!/usr/bin/env python3
"""Optional system tray icon for ClaudeGuard on Linux.

Requires pystray and Pillow.  Loaded only when the daemon starts with --tray.
"""
import threading

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    raise ImportError("pystray and Pillow are required for tray mode. "
                      "Install: pip install pystray Pillow")


def _create_icon(state):
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    colors = {
        "allowed":  (0, 180, 0, 255),
        "blocked":  (220, 0, 0, 255),
        "offline":  (160, 160, 160, 255),
        "disabled": (200, 180, 0, 255),
    }
    fill = colors.get(state, (160, 160, 160, 255))

    # Shield-ish shape
    m = 4
    cx, cy = size // 2, size // 2
    draw.polygon([
        (cx, m),
        (size - m, m + 12),
        (size - m, cy + 6),
        (cx, size - m),
        (m, cy + 6),
        (m, m + 12),
    ], fill=fill)
    return img


class TrayIcon:
    def __init__(self, daemon):
        self.daemon = daemon
        self._icon = None

    # -- menu -----------------------------------------------------------------

    def _build_menu(self):
        d = self.daemon
        items = [
            pystray.MenuItem(self._status_text(), None, enabled=False),
            pystray.MenuItem(f"🌐 Current IP: {d.current_ip}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "🔄 Re-check Now",
                lambda _i: threading.Thread(target=d.recheck, daemon=True).start(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "✓ Protection Active" if d.protection_enabled else "Protection Disabled",
                lambda _i: self._toggle_protection(),
                checked=lambda _i: d.protection_enabled,
            ),
            pystray.MenuItem(
                "✓ Block Auto-Updates" if d.block_auto_updates else "Block Auto-Updates",
                lambda _i: self._toggle_updates(),
                checked=lambda _i: d.block_auto_updates,
            ),
            pystray.Menu.SEPARATOR,
        ]

        if d.model_override_enabled:
            items.append(pystray.MenuItem(
                f"✓ Default Model: {d.default_model}",
                lambda _i: self._toggle_model(),
                checked=lambda _i: d.model_override_enabled,
            ))
        else:
            items.append(pystray.MenuItem(
                "Model Override (off)",
                lambda _i: self._toggle_model(),
                checked=lambda _i: d.model_override_enabled,
            ))

        items.append(pystray.Menu.SEPARATOR)

        if (d.current_ip and d.current_ip not in ("No Internet", "...")
                and d.current_ip not in d.allowed_ips):
            items.append(pystray.MenuItem(
                f"➕ Whitelist Current IP ({d.current_ip})",
                lambda _i: self._add_current_ip(),
            ))

        items.append(pystray.MenuItem("Quit ClaudeGuard", lambda _i: self._quit()))
        return pystray.Menu(*items)

    def _status_text(self):
        labels = {
            "offline":  "Status: ⚪ Offline (no connection)",
            "disabled": "Status: 🟡 Protection Disabled",
            "allowed":  "Status: 🟢 Protected (VPN OK)",
            "blocked":  "Status: 🔴 BLOCKED (unallowed network)",
        }
        return labels.get(self.daemon.state, "Status: Unknown")

    # -- actions --------------------------------------------------------------

    def _toggle_protection(self):
        d = self.daemon
        d.protection_enabled = not d.protection_enabled
        d.config.protection_enabled = d.protection_enabled
        threading.Thread(target=d.recheck, daemon=True).start()
        self._refresh()

    def _toggle_updates(self):
        d = self.daemon
        d.block_auto_updates = not d.block_auto_updates
        d.config.block_auto_updates = d.block_auto_updates
        threading.Thread(target=d.recheck, daemon=True).start()
        self._refresh()

    def _toggle_model(self):
        d = self.daemon
        d.model_override_enabled = not d.model_override_enabled
        d.config.model_override_enabled = d.model_override_enabled
        self._refresh()

    def _add_current_ip(self):
        d = self.daemon
        if d.current_ip and d.current_ip not in ("No Internet", "..."):
            d.config.add_ip(d.current_ip)
            d.allowed_ips = d.config.allowed_ips
            threading.Thread(target=d.recheck, daemon=True).start()
            self._refresh()

    def _quit(self):
        self.daemon.stop()
        if self._icon:
            self._icon.stop()

    # -- updates from daemon --------------------------------------------------

    def update_state(self, state, ip):
        if self._icon:
            self._icon.icon = _create_icon(state)
            self._refresh()

    def _refresh(self):
        if self._icon:
            self._icon.menu = self._build_menu()
            self._icon.update_menu()

    # -- lifecycle ------------------------------------------------------------

    def run(self):
        self._icon = pystray.Icon(
            "ClaudeGuard",
            _create_icon(self.daemon.state),
            "ClaudeGuard",
            menu=self._build_menu(),
        )
        self._icon.run()

    def stop(self):
        if self._icon:
            self._icon.stop()
