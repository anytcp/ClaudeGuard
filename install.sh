#!/usr/bin/env bash
#
# ClaudeGuard installer for Linux (Arch / Debian / Fedora / etc.)
#
# Two ways to install — both give the exact same result.
#
#   One-liner:  bash -c "$(curl -fsSL https://raw.githubusercontent.com/YOU/ClaudeGuard/main/install.sh)"
#   Local:      ./install.sh
#
# Compiles the C root helper on this machine with gcc/clang. No macOS deps.
set -euo pipefail

REPO="${CLAUDEGUARD_REPO:-anytcp/ClaudeGuard}"
BRANCH="${CLAUDEGUARD_BRANCH:-main}"

INSTALL_DIR="$HOME/.local/share/ClaudeGuard"
CONFIG_DIR="$HOME/.config/claudeguard"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
HELPER_SYS_DIR="/var/lib/claudeguard"

# --- Pretty output ----------------------------------------------------------
if [ -t 1 ]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'
    YLW=$'\033[33m'; CYN=$'\033[36m'; RST=$'\033[0m'
else
    BOLD=; DIM=; RED=; GRN=; YLW=; CYN=; RST=
fi
info() { printf '%s %s\n' "${CYN}==>${RST}" "$*"; }
ok()   { printf '%s %s\n' "${GRN}+${RST}"   "$*"; }
warn() { printf '%s %s\n' "${YLW}!${RST}"   "$*"; }
die()  { printf '%s %s\n' "${RED}x${RST}"   "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

printf '%s\n' "${BOLD}${CYN}"
cat <<'BANNER'
   ____ _                 _        ____                     _
  / ___| | __ _ _   _  __| | ___  / ___|_   _  __ _ _ __ __| |
 | |   | |/ _` | | | |/ _` |/ _ \| |  _| | | |/ _` | '__/ _` |
 | |___| | (_| | |_| | (_| |  __/| |_| | |_| | (_| | | | (_| |
  \____|_|\__,_|\__,_|\__,_|\___| \____|\__,_|\__,_|_|  \__,_|
BANNER
printf '%s\n' "        VPN-whitelist guard for Claude on Linux${RST}"
printf '\n'

[ "$(uname -s)" = "Linux" ] || die "This installer is for Linux only."

# --- Detect package manager --------------------------------------------------
detect_pm() {
    if [ -e /etc/NIXOS ] || have nixos-rebuild; then echo "nix"; return; fi
    if have pacman;  then echo "pacman";  return; fi
    if have apt-get; then echo "apt";     return; fi
    if have dnf;     then echo "dnf";     return; fi
    if have zypper;  then echo "zypper";  return; fi
    echo "unknown"
}
PM="$(detect_pm)"
info "Detected package manager: ${BOLD}$PM${RST}"

# --- Bootstrap: fetch source when piped from curl -----------------------------
_src="${BASH_SOURCE[0]:-}"
if [ -n "$_src" ] && [ -f "$_src" ]; then
    SOURCE_DIR="$(cd "$(dirname "$_src")" && pwd)"
else
    SOURCE_DIR=""
fi

if [ -z "$SOURCE_DIR" ] || [ ! -f "$SOURCE_DIR/src/daemon.py" ]; then
    info "Fetching ClaudeGuard from ${BOLD}github.com/$REPO${RST} (branch $BRANCH)..."
    BOOT_TMP="$(mktemp -d)"
    trap 'rm -rf "$BOOT_TMP"' EXIT
    if have git; then
        git clone --depth 1 --branch "$BRANCH" "https://github.com/$REPO.git" \
            "$BOOT_TMP/ClaudeGuard" >/dev/null 2>&1 || die "git clone failed"
        SOURCE_DIR="$BOOT_TMP/ClaudeGuard"
    else
        curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" \
            | tar -xz -C "$BOOT_TMP" || die "download failed"
        SOURCE_DIR="$BOOT_TMP/${REPO##*/}-$BRANCH"
    fi
    [ -f "$SOURCE_DIR/src/daemon.py" ] || die "fetched archive is missing src/daemon.py"
    ok "Source ready."
fi

# --- Prerequisite: C compiler -------------------------------------------------
if ! have gcc && ! have clang; then
    info "Installing C compiler..."
    case "$PM" in
        pacman)  sudo pacman -S --noconfirm gcc ;;
        apt)     sudo apt-get update && sudo apt-get install -y gcc ;;
        dnf)     sudo dnf install -y gcc ;;
        zypper)  sudo zypper install -y gcc ;;
        nix)     nix-env -iA nixpkgs.gcc ;;
        *)       die "Install gcc or clang manually, then re-run." ;;
    esac
fi

# --- Prerequisite: python3 ---------------------------------------------------
if ! have python3; then
    info "Installing python3..."
    case "$PM" in
        pacman)  sudo pacman -S --noconfirm python ;;
        apt)     sudo apt-get update && sudo apt-get install -y python3 ;;
        dnf)     sudo dnf install -y python3 ;;
        zypper)  sudo zypper install -y python3 ;;
        nix)     nix-env -iA nixpkgs.python3 ;;
        *)       die "Install python3 manually, then re-run." ;;
    esac
fi

# --- Prerequisite: iptables ---------------------------------------------------
if ! have iptables; then
    info "Installing iptables..."
    case "$PM" in
        pacman)  sudo pacman -S --noconfirm iptables ;;
        apt)     sudo apt-get update && sudo apt-get install -y iptables ;;
        dnf)     sudo dnf install -y iptables ;;
        zypper)  sudo zypper install -y iptables ;;
        nix)     nix-env -iA nixpkgs.iptables ;;
        *)       warn "iptables not found. Install it for firewall enforcement." ;;
    esac
fi

# --- Detect display server for tray mode -------------------------------------
HAS_DISPLAY=false
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    HAS_DISPLAY=true
fi

INSTALL_TRAY=false
if [ "$HAS_DISPLAY" = true ]; then
    # Check if pystray deps are available or can be installed
    CAN_TRAY=false
    if python3 -c "import pystray, PIL" 2>/dev/null; then
        CAN_TRAY=true
    fi

    if [ "$CAN_TRAY" = true ]; then
        printf '\n%sDo you want to install the system tray version? [Y/n] %s' "$BOLD" "$RST"
        if [ -r /dev/tty ]; then
            read -r ans < /dev/tty || ans=""
        else
            ans="y"
        fi
        case "$ans" in
            [Nn]*) INSTALL_TRAY=false ;;
            *)     INSTALL_TRAY=true ;;
        esac
    else
        printf '\n%sDo you want to install the system tray version? %s\n' "$BOLD" "$RST"
        printf '%s(Requires pystray + Pillow — will be installed via pip) [y/N] %s' "$DIM" "$RST"
        if [ -r /dev/tty ]; then
            read -r ans < /dev/tty || ans=""
        else
            ans="n"
        fi
        case "$ans" in
            [Yy]*)
                info "Installing pystray and Pillow..."
                _tray_ok=false
                # 1. Try system package manager first (Arch ships python-pillow)
                case "$PM" in
                    pacman)
                        sudo pacman -S --noconfirm --needed python-pillow 2>/dev/null || true
                        python3 -m pip install --user --break-system-packages pystray 2>/dev/null && _tray_ok=true
                        ;;
                    apt)
                        sudo apt-get install -y python3-pil 2>/dev/null || true
                        python3 -m pip install --user --break-system-packages pystray 2>/dev/null && _tray_ok=true
                        ;;
                    nix)
                        nix-env -iA nixpkgs.python3Packages.pillow 2>/dev/null || true
                        nix-env -iA nixpkgs.python3Packages.pystray 2>/dev/null && _tray_ok=true
                        # Fallback to pip if not in nixpkgs
                        if [ "$_tray_ok" = false ]; then
                            python3 -m pip install --user pystray Pillow 2>/dev/null && _tray_ok=true
                        fi
                        ;;
                    *)
                        python3 -m pip install --user --break-system-packages pystray Pillow 2>/dev/null && _tray_ok=true
                        ;;
                esac
                # 2. Fallback: plain pip without --break-system-packages
                if [ "$_tray_ok" = false ]; then
                    python3 -m pip install --user pystray Pillow 2>/dev/null && _tray_ok=true
                fi
                if python3 -c "import pystray, PIL" 2>/dev/null; then
                    INSTALL_TRAY=true
                    ok "pystray + Pillow installed."
                else
                    warn "pystray import failed. Using headless (systemd) mode."
                fi
                ;;
            *)
                INSTALL_TRAY=false ;;
        esac
    fi
else
    info "No display server detected. Installing headless (systemd) daemon."
fi

# --- 1. Stage source into install dir ----------------------------------------
info "Installing to ${BOLD}$INSTALL_DIR${RST}"
mkdir -p "$INSTALL_DIR/bin" "$INSTALL_DIR/src"
if have rsync; then
    rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
        "$SOURCE_DIR/src/" "$INSTALL_DIR/src/"
    rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude 'hosts-helper' \
        "$SOURCE_DIR/bin/" "$INSTALL_DIR/bin/"
else
    find "$SOURCE_DIR/src" -maxdepth 1 -name '*.py' -exec cp {} "$INSTALL_DIR/src/" \;
    find "$SOURCE_DIR/src" -maxdepth 1 -name '*.c' -exec cp {} "$INSTALL_DIR/src/" \;
    for f in "$SOURCE_DIR/bin/"*; do
        [ -d "$f" ] && continue
        [ "$(basename "$f")" = "hosts-helper" ] && continue
        cp "$f" "$INSTALL_DIR/bin/"
    done
fi

# --- 2. Compile C helper -----------------------------------------------------
info "Compiling privileged hosts helper (C)..."
CC="gcc"
have clang && CC="clang"
rm -f "$INSTALL_DIR/bin/hosts-helper" 2>/dev/null || true
$CC -O2 "$SOURCE_DIR/src/hosts_helper.c" -o "$INSTALL_DIR/bin/hosts-helper"

chmod +x "$INSTALL_DIR/bin/claudeguard" \
         "$INSTALL_DIR/src/cli_wrapper.py" "$INSTALL_DIR/src/app_launcher.py" \
         "$INSTALL_DIR/src/helper.py" "$INSTALL_DIR/src/daemon.py"

# --- 3. Symlink the manager CLI ----------------------------------------------
info "Installing the 'claudeguard' manager..."
mkdir -p "$HOME/.local/bin" "$CONFIG_DIR"
ln -sf "$INSTALL_DIR/bin/claudeguard" "$HOME/.local/bin/claudeguard"
if [ -w "/usr/local/bin" ]; then
    ln -sf "$INSTALL_DIR/bin/claudeguard" "/usr/local/bin/claudeguard"
fi

# --- 4. Hook the `claude` CLI + locate Claude Desktop -------------------------
info "Hooking the 'claude' command..."
REPAIR_OUT="$(cd "$INSTALL_DIR" && python3 -c "
import sys
sys.path.insert(0, '.')
from src.config import ConfigManager
from src.integrity import repair

report = repair(ConfigManager())
print('REAL_CLI=' + (report['real_cli'] or ''))
for change in report['changed']:
    print('CHANGED=' + change)
for note in report['notes']:
    print('NOTE=' + note)
" 2>&1)" || REPAIR_OUT=""

REAL_CLAUDE="$(printf '%s\n' "$REPAIR_OUT" | sed -n 's/^REAL_CLI=//p')"
printf '%s\n' "$REPAIR_OUT" | sed -n 's/^CHANGED=//p' | while IFS= read -r line; do ok "$line"; done
printf '%s\n' "$REPAIR_OUT" | sed -n 's/^NOTE=//p'    | while IFS= read -r line; do warn "$line"; done

if [ -n "$REAL_CLAUDE" ]; then
    ok "Real Claude CLI: $REAL_CLAUDE"
else
    warn "Could not find the real 'claude' CLI. If you use Claude Code, run later:"
    warn "    claudeguard set-cli-path /path/to/real/claude"
fi

# --- 5. systemd user service (daemon) ----------------------------------------
info "Creating systemd user service..."
mkdir -p "$SYSTEMD_USER_DIR"

DAEMON_ARGS=""
if [ "$INSTALL_TRAY" = true ]; then
    DAEMON_ARGS=" --tray"
fi

cat > "$SYSTEMD_USER_DIR/claudeguard.service" <<EOF
[Unit]
Description=ClaudeGuard VPN whitelist daemon
After=network-online.target

[Service]
Type=simple
ExecStart=$(command -v python3) $INSTALL_DIR/src/daemon.py${DAEMON_ARGS}
Restart=on-failure
RestartSec=5
Environment=DISPLAY=${DISPLAY:-}
Environment=WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-}
Environment=DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-}
Environment=XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable claudeguard.service 2>/dev/null || true
systemctl --user restart claudeguard.service 2>/dev/null || true
ok "systemd user service installed and started."

# --- 6. Root helper via systemd (path + service) ------------------------------
info "Installing the root network helper (needs your password once)..."
mkdir -p "$CONFIG_DIR/pending"
chmod 700 "$CONFIG_DIR/pending"
[ -f "$CONFIG_DIR/pending/request" ] || : > "$CONFIG_DIR/pending/request"

HELPER_SERVICE="[Unit]
Description=ClaudeGuard root helper

[Service]
Type=oneshot
ExecStart=$HELPER_SYS_DIR/hosts-helper $CONFIG_DIR"

HELPER_PATH="[Unit]
Description=ClaudeGuard root helper trigger

[Path]
PathChanged=$CONFIG_DIR/pending/request

[Install]
WantedBy=multi-user.target"

install_root_helper() {
    local S="$1"
    $S mkdir -p "$HELPER_SYS_DIR"
    $S cp "$INSTALL_DIR/bin/hosts-helper" "$HELPER_SYS_DIR/hosts-helper"
    $S chown root:root "$HELPER_SYS_DIR" "$HELPER_SYS_DIR/hosts-helper"
    $S chmod 755 "$HELPER_SYS_DIR/hosts-helper"
    printf '%s\n' "$HELPER_SERVICE" | $S tee /etc/systemd/system/claudeguard-helper.service >/dev/null
    printf '%s\n' "$HELPER_PATH"    | $S tee /etc/systemd/system/claudeguard-helper.path    >/dev/null
    $S systemctl daemon-reload
    $S systemctl enable claudeguard-helper.path 2>/dev/null || true
    $S systemctl restart claudeguard-helper.path 2>/dev/null || true
}

if [ "$(id -u)" -eq 0 ]; then
    install_root_helper ""
else
    install_root_helper "sudo"
fi
rm -f "$INSTALL_DIR/bin/hosts-helper" 2>/dev/null || true

# --- 7. First-run: offer to whitelist the current public IP -------------------
CUR_IP="$(cd "$INSTALL_DIR" && python3 -c 'import sys; sys.path.insert(0,"."); from src.ip_checker import stun_public_ip; print(stun_public_ip() or "")' 2>/dev/null || true)"
if [ -n "$CUR_IP" ] && [ -r /dev/tty ]; then
    printf '\n%sYour current public IP is %s%s%s.\n' "$BOLD" "$CYN" "$CUR_IP" "$RST"
    printf '%sWhitelist it now? Only say yes if you are on your VPN right now. [y/N] %s' "$BOLD" "$RST"
    read -r ans < /dev/tty || ans=""
    case "$ans" in
        [Yy]*) "$INSTALL_DIR/bin/claudeguard" add-ip "$CUR_IP" >/dev/null 2>&1 && ok "Whitelisted $CUR_IP" ;;
        *)     info "Skipped. Add your VPN IP later: ${BOLD}claudeguard add-ip <IP>${RST}" ;;
    esac
fi

printf '\n%s\n' "${GRN}${BOLD}ClaudeGuard installed & running.${RST}"
if [ "$INSTALL_TRAY" = true ]; then
    printf '%s\n' "   Mode: system tray (look for the shield icon)."
else
    printf '%s\n' "   Mode: headless systemd daemon."
fi
printf '%s\n' "   ${DIM}Manage it:${RST} ${BOLD}claudeguard status${RST}  |  ${BOLD}claudeguard add-ip <IP>${RST}"
if ! printf '%s' "$PATH" | tr ':' '\n' | grep -qx "$HOME/.local/bin"; then
    printf '%s\n' "   ${YLW}Note:${RST} add ~/.local/bin to your PATH:"
    SHELL_RC="$HOME/.bashrc"
    [ -n "${ZSH_VERSION:-}" ] || [ "$(basename "${SHELL:-}")" = "zsh" ] && SHELL_RC="$HOME/.zshrc"
    printf '%s\n' "         ${DIM}echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> $SHELL_RC${RST}"
fi
