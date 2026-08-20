"""Keeps ClaudeGuard attached to Claude across reinstalls, updates and moves.

Every hook we own points at something Anthropic's installers rewrite under us:
the `claude` shim (a reinstall drops the real binary back on top of it), the
version-stamped path we hand off to, and Claude Desktop's Electron binary. Pinning any
of them means the guard silently detaches while the daemon still says allowed — so
everything here is discovered, and `repair()` re-attaches what came loose.
"""
import glob
import os
import re
import subprocess

HOME = os.path.expanduser("~")
INSTALL_DIR = os.path.join(HOME, ".local", "share", "ClaudeGuard")

PRIMARY_LINK_DIR = os.path.join(HOME, ".local", "bin")
CLI_LINK_DIRS = [PRIMARY_LINK_DIR, "/usr/local/bin"]


# --- our own wrapper --------------------------------------------------------

def wrapper_path():
    """The cli_wrapper.py shims point at: the installed copy, else this checkout."""
    installed = os.path.join(INSTALL_DIR, "src", "cli_wrapper.py")
    if os.path.isfile(installed):
        return installed
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), "cli_wrapper.py")


def is_our_wrapper(path):
    """True if `path` is a ClaudeGuard shim rather than a real Claude binary."""
    if not path:
        return False
    try:
        real = os.path.realpath(path)
    except OSError:
        return False
    if os.path.basename(real) == "cli_wrapper.py":
        return True
    try:
        if os.path.isfile(real) and os.path.getsize(real) < 200_000:
            with open(real, "rb") as f:
                if b"ClaudeGuard" in f.read(4096):
                    return True
    except OSError:
        pass
    return False


# --- real `claude` CLI discovery -------------------------------------------

def _version_key(text):
    return tuple(int(n) for n in re.findall(r"\d+", text)) or (0,)


def _native_binaries():
    """Claude Code native install paths on Linux (incl. NixOS)."""
    patterns = [
        os.path.join(HOME, ".local", "share", "claude", "versions", "*", "claude"),
        os.path.join(HOME, ".local", "share", "claude", "*", "claude"),
        os.path.join(HOME, ".local", "share", "claude", "claude"),
        os.path.join(HOME, ".claude", "local", "claude"),
        os.path.join(HOME, ".claude", "local", "node_modules", ".bin", "claude"),
        # NixOS
        os.path.join(HOME, ".nix-profile", "bin", "claude"),
        "/nix/store/*/bin/claude",
        "/run/current-system/sw/bin/claude",
    ]
    found = []
    for pattern in patterns:
        found.extend(sorted(glob.glob(pattern), key=_version_key, reverse=True))
    return found


def _path_binaries():
    dirs = list(CLI_LINK_DIRS)
    dirs += [d for d in os.environ.get("PATH", "").split(":") if d]
    dirs += [
        os.path.join(HOME, ".bun", "bin"),
        os.path.join(HOME, ".volta", "bin"),
        os.path.join(HOME, ".npm-global", "bin"),
        os.path.join(HOME, ".nvm", "versions", "node"),
        os.path.join(HOME, ".nix-profile", "bin"),
        "/run/current-system/sw/bin",
        "/usr/bin",
        "/snap/bin",
    ]
    seen, found = set(), []
    for d in dirs:
        if not d or d in seen:
            continue
        seen.add(d)
        found.append(os.path.join(d, "claude"))
    return found


def _usable_real_cli(path):
    if not path:
        return None
    try:
        real = os.path.realpath(path)
    except OSError:
        return None
    if not os.path.isfile(real) or not os.access(real, os.X_OK):
        return None
    if is_our_wrapper(real):
        return None
    return real


def discover_real_cli(config=None):
    """The real `claude` binary, or None."""
    candidates = []
    if config is not None:
        candidates.append(config.config.get("real_claude_cli_path"))
    candidates += _native_binaries() + _path_binaries()
    for candidate in candidates:
        real = _usable_real_cli(candidate)
        if real:
            return real
    return None


# --- Claude Desktop discovery (Electron on Linux) --------------------------

DESKTOP_PROCESS_PATTERNS = [
    "claude-desktop",
    "Claude.*electron",
    "claude.*Electron",
]

DESKTOP_SEARCH_PATHS = [
    "/opt/Claude",
    "/opt/claude-desktop",
    "/usr/lib/claude-desktop",
    "/usr/share/claude-desktop",
    os.path.join(HOME, ".local", "share", "claude-desktop"),
    "/snap/claude-desktop/current",
    "/var/lib/flatpak/app/com.anthropic.claude",
    os.path.join(HOME, ".local", "share", "flatpak", "app", "com.anthropic.claude"),
    # NixOS
    os.path.join(HOME, ".nix-profile", "share", "claude-desktop"),
    "/run/current-system/sw/share/claude-desktop",
]

DESKTOP_DESKTOP_FILES = [
    "/usr/share/applications/claude-desktop.desktop",
    os.path.join(HOME, ".local", "share", "applications", "claude-desktop.desktop"),
]


def _parse_desktop_file_exec(desktop_file):
    """Extract Exec= path from a .desktop file."""
    try:
        with open(desktop_file, "r") as f:
            for line in f:
                if line.startswith("Exec="):
                    cmd = line[5:].strip().split()[0]
                    if os.path.isfile(cmd):
                        return cmd
    except Exception:
        pass
    return None


def discover_desktop_app(config=None):
    """Claude Desktop (Electron) on Linux, or None."""
    candidates = []
    if config is not None:
        app = config.config.get("real_claude_app_path")
        if app:
            candidates.append(app)

    for path in DESKTOP_SEARCH_PATHS:
        if os.path.isdir(path):
            candidates.append(path)
        for name in ("claude-desktop", "Claude", "claude"):
            full = os.path.join(path, name)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                return full

    for df in DESKTOP_DESKTOP_FILES:
        exe = _parse_desktop_file_exec(df)
        if exe:
            return exe

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate

    try:
        result = subprocess.run(["which", "claude-desktop"],
                                capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            path = result.stdout.strip()
            if path and os.path.isfile(path):
                return path
    except Exception:
        pass

    return None


def desktop_update_paths():
    """Claude Desktop's auto-update state on Linux."""
    paths = []
    for pattern in [
        os.path.join(HOME, ".config", "claude-desktop", "app-update.yml"),
        os.path.join(HOME, ".config", "Claude", "app-update.yml"),
        os.path.join(HOME, ".local", "share", "claude-desktop", "app-update.yml"),
    ]:
        if os.path.exists(pattern):
            paths.append(pattern)
    for pattern in [
        os.path.join(HOME, ".cache", "claude-desktop"),
        os.path.join(HOME, ".cache", "Claude"),
    ]:
        matches = glob.glob(pattern + "*/update*")
        paths.extend(matches)
    return paths


# --- repair -----------------------------------------------------------------

def _relink(wrapper, link):
    tmp = link + ".claudeguard.tmp"
    try:
        if os.path.lexists(tmp):
            os.remove(tmp)
        os.symlink(wrapper, tmp)
        os.replace(tmp, link)
        return True
    except OSError:
        try:
            if os.path.lexists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def repair_cli(config, relink=True):
    """Re-point the config at a real `claude`, then re-hook the shims."""
    report = {"real_cli": None, "changed": [], "notes": []}

    real = discover_real_cli(config)
    report["real_cli"] = real
    if real:
        if config.config.get("real_claude_cli_path") != real:
            config.config["real_claude_cli_path"] = real
            config.save_config()
            report["changed"].append(f"hand-off target → {real}")
    else:
        report["notes"].append("real 'claude' binary not found — install Claude Code, "
                               "or run: claudeguard set-cli-path <path>")

    if not relink:
        return report

    wrapper = wrapper_path()
    if not os.path.isfile(wrapper):
        report["notes"].append(f"wrapper missing at {wrapper} — reinstall ClaudeGuard")
        return report

    for link_dir in CLI_LINK_DIRS:
        link = os.path.join(link_dir, "claude")
        exists = os.path.lexists(link)

        if not exists:
            if link_dir != PRIMARY_LINK_DIR:
                continue
            try:
                os.makedirs(link_dir, exist_ok=True)
            except OSError:
                report["notes"].append(f"cannot create {link_dir}")
                continue
        elif is_our_wrapper(link) and os.path.realpath(link) == os.path.realpath(wrapper):
            continue

        if exists and not real:
            report["notes"].append(f"{link} left in place (real CLI unknown)")
            continue

        if exists and not os.path.islink(link) and not is_our_wrapper(link):
            report["notes"].append(f"{link} is a real binary, not a symlink — not replaced")
            continue

        if _relink(wrapper, link):
            report["changed"].append(f"re-hooked {link}")
        elif not exists:
            report["notes"].append(f"cannot write {link}")
        else:
            report["notes"].append(f"cannot replace {link} (no permission)")

    return report


def repair_desktop(config):
    report = {"desktop_app": None, "changed": [], "notes": []}
    app = discover_desktop_app(config)
    report["desktop_app"] = app
    if app:
        if config.config.get("real_claude_app_path") != app:
            config.config["real_claude_app_path"] = app
            config.save_config()
            report["changed"].append(f"Claude Desktop → {app}")
    else:
        report["notes"].append("Claude Desktop not installed (nothing to guard)")
    return report


def repair(config):
    """Full self-heal."""
    cli = repair_cli(config)
    desktop = repair_desktop(config)
    return {
        "real_cli": cli["real_cli"],
        "desktop_app": desktop["desktop_app"],
        "changed": cli["changed"] + desktop["changed"],
        "notes": cli["notes"] + desktop["notes"],
    }


def cli_hook_status():
    """(intercepted, details) for `claudeguard doctor`."""
    details = []
    intercepted = True
    path_dirs = [d for d in os.environ.get("PATH", "").split(":") if d]
    for link_dir in CLI_LINK_DIRS:
        link = os.path.join(link_dir, "claude")
        if not os.path.lexists(link):
            continue
        ours = is_our_wrapper(link)
        on_path = link_dir in path_dirs
        details.append({"path": link, "guarded": ours, "on_path": on_path})
        if on_path and not ours:
            intercepted = False
    if not details:
        intercepted = False
    return intercepted, details
