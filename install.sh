#!/usr/bin/env bash
#
# ClaudeGuard installer.
#
# Works two ways from the SAME file:
#   • One-liner:   bash -c "$(curl -fsSL https://raw.githubusercontent.com/ivblz/ClaudeGuard/main/install.sh)"
#   • Local:       ./install.sh   (from a git checkout or unpacked archive)
#
# When run via curl|bash there's no local source tree, so the script first
# fetches the repo, then compiles the Swift menu bar app + C helper NATIVELY on
# this Mac (Apple Silicon or Intel). Locally-compiled binaries carry no
# quarantine flag, so Gatekeeper never blocks them — no Apple Developer account,
# no code-signing, no notarization required.
set -euo pipefail

# Which repo the one-liner pulls from. Override without editing:
#   CLAUDEGUARD_REPO=you/ClaudeGuard bash -c "$(curl -fsSL .../install.sh)"
REPO="${CLAUDEGUARD_REPO:-ivblz/ClaudeGuard}"
BRANCH="${CLAUDEGUARD_BRANCH:-main}"

INSTALL_DIR="$HOME/.local/share/ClaudeGuard"
APP_BUNDLE="$INSTALL_DIR/ClaudeGuard.app"
PLIST_PATH="$HOME/Library/LaunchAgents/com.claudeguard.daemon.plist"
SUDOERS_FILE="/etc/sudoers.d/claudeguard"

# --- Pretty output ----------------------------------------------------------
if [ -t 1 ]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'
    YLW=$'\033[33m'; CYN=$'\033[36m'; RST=$'\033[0m'
else
    BOLD=; DIM=; RED=; GRN=; YLW=; CYN=; RST=
fi
info() { printf '%s %s\n' "${CYN}==>${RST}" "$*"; }
ok()   { printf '%s %s\n' "${GRN}✓${RST}"   "$*"; }
warn() { printf '%s %s\n' "${YLW}⚠${RST}"   "$*"; }
die()  { printf '%s %s\n' "${RED}✗${RST}"   "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

printf '%s\n' "${BOLD}${CYN}"
cat <<'BANNER'
   ____ _                 _        ____                     _
  / ___| | __ _ _   _  __| | ___  / ___|_   _  __ _ _ __ __| |
 | |   | |/ _` | | | |/ _` |/ _ \| |  _| | | |/ _` | '__/ _` |
 | |___| | (_| | |_| | (_| |  __/| |_| | |_| | (_| | | | (_| |
  \____|_|\__,_|\__,_|\__,_|\___| \____|\__,_|\__,_|_|  \__,_|
BANNER
printf '%s\n' "        VPN-whitelist guard for Claude on macOS${RST}"
printf '\n'

[ "$(uname -s)" = "Darwin" ] || die "ClaudeGuard is macOS-only."

# --- Bootstrap: fetch source when piped from curl (no local tree) -----------
_src="${BASH_SOURCE[0]:-}"
if [ -n "$_src" ] && [ -f "$_src" ]; then
    SOURCE_DIR="$(cd "$(dirname "$_src")" && pwd)"
else
    SOURCE_DIR=""
fi

if [ -z "$SOURCE_DIR" ] || [ ! -f "$SOURCE_DIR/src/main.swift" ]; then
    info "Fetching ClaudeGuard from ${BOLD}github.com/$REPO${RST} (branch $BRANCH)…"
    BOOT_TMP="$(mktemp -d)"
    trap 'rm -rf "$BOOT_TMP"' EXIT
    if have git; then
        git clone --depth 1 --branch "$BRANCH" "https://github.com/$REPO.git" \
            "$BOOT_TMP/ClaudeGuard" >/dev/null 2>&1 || die "git clone failed (is $REPO correct and public?)"
        SOURCE_DIR="$BOOT_TMP/ClaudeGuard"
    else
        curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" \
            | tar -xz -C "$BOOT_TMP" || die "download failed (is $REPO correct and public?)"
        SOURCE_DIR="$BOOT_TMP/${REPO##*/}-$BRANCH"
    fi
    [ -f "$SOURCE_DIR/src/main.swift" ] || die "fetched archive is missing src/main.swift"
    ok "Source ready."
fi

LOGO_PNG="$SOURCE_DIR/assets/AppIcon.png"

# --- Prerequisite: Xcode Command Line Tools (clang + swiftc) ----------------
# Required to compile on-device. If missing, launch Apple's installer and wait.
if ! have clang || ! have swiftc; then
    warn "Xcode Command Line Tools required (clang + swiftc)."
    info "Launching Apple's installer — click ${BOLD}Install${RST} in the popup."
    xcode-select --install >/dev/null 2>&1 || true
    printf '%s' "${DIM}    waiting for the tools to finish installing"
    while ! (have clang && have swiftc); do printf '.'; sleep 5; done
    printf '%s\n' "${RST}"
    ok "Command Line Tools ready."
fi

# --- 1. Stage source into the install dir -----------------------------------
info "Installing to ${BOLD}$INSTALL_DIR${RST}"
mkdir -p "$INSTALL_DIR/bin" "$INSTALL_DIR/src"
/usr/bin/rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
    "$SOURCE_DIR/src/" "$INSTALL_DIR/src/"
for f in "$SOURCE_DIR/bin/"*; do
    [ "$(basename "$f")" = "hosts-helper" ] && continue   # compiled below, never shipped
    cp -R "$f" "$INSTALL_DIR/bin/"
done

# --- 2. Compile the native binaries -----------------------------------------
# A previous install may have left a root-owned setuid hosts-helper behind; as a
# regular user we can only remove-then-recreate (dir write perm governs unlink).
info "Compiling privileged hosts helper (C)…"
rm -f "$INSTALL_DIR/bin/hosts-helper" 2>/dev/null || sudo rm -f "$INSTALL_DIR/bin/hosts-helper"
clang -O2 "$SOURCE_DIR/src/hosts_helper.c" -o "$INSTALL_DIR/bin/hosts-helper"

info "Compiling native menu bar app (Swift)…"
swiftc -O "$SOURCE_DIR/src/main.swift" -o "$INSTALL_DIR/bin/ClaudeGuardMenuBar"

chmod +x "$INSTALL_DIR/bin/claudeguard" "$INSTALL_DIR/bin/ClaudeGuardMenuBar" \
         "$INSTALL_DIR/src/cli_wrapper.py" "$INSTALL_DIR/src/app_launcher.py" \
         "$INSTALL_DIR/src/helper.py"

# --- 3. Build the .app bundle (icon + Info.plist) ---------------------------
info "Creating ClaudeGuard.app bundle…"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"

if [ -f "$LOGO_PNG" ]; then
    ICONSET_DIR="/tmp/AppIcon.iconset"; rm -rf "$ICONSET_DIR"; mkdir -p "$ICONSET_DIR"
    for pair in "16:icon_16x16" "32:icon_16x16@2x" "32:icon_32x32" "64:icon_32x32@2x" \
                "128:icon_128x128" "256:icon_128x128@2x" "256:icon_256x256" \
                "512:icon_256x256@2x" "512:icon_512x512" "1024:icon_512x512@2x"; do
        sz="${pair%%:*}"; nm="${pair#*:}"   # split without relying on word-splitting (bash+zsh safe)
        sips -z "$sz" "$sz" "$LOGO_PNG" --out "$ICONSET_DIR/$nm.png" >/dev/null 2>&1
    done
    iconutil -c icns "$ICONSET_DIR" -o "$APP_BUNDLE/Contents/Resources/AppIcon.icns"
    rm -rf "$ICONSET_DIR"
else
    warn "assets/AppIcon.png not found — bundle will use the default icon."
fi

cat > "$APP_BUNDLE/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>ClaudeGuardMenuBar</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundleIdentifier</key><string>com.claudeguard.app</string>
    <key>CFBundleName</key><string>ClaudeGuard</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
EOF

cp "$INSTALL_DIR/bin/ClaudeGuardMenuBar" "$APP_BUNDLE/Contents/MacOS/ClaudeGuardMenuBar"
mkdir -p "$HOME/Applications"
ln -sf "$APP_BUNDLE" "$HOME/Applications/ClaudeGuard.app"

# --- 4. Locate the real `claude` CLI so the wrapper can hand off to it -------
REAL_CLAUDE=""
for candidate in "/opt/homebrew/bin/claude" "/usr/local/bin/claude" "$HOME/.local/bin/claude"; do
    if [ -L "$candidate" ]; then
        resolved=$(readlink -f "$candidate" 2>/dev/null || python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$candidate")
        case "$resolved" in
            "$INSTALL_DIR"/*|"$SOURCE_DIR"/*) ;;                 # our own wrapper, skip
            *) [ -f "$resolved" ] && { REAL_CLAUDE="$resolved"; break; } ;;
        esac
    fi
done
if [ -z "$REAL_CLAUDE" ]; then
    REAL_CLAUDE=$(find /opt/homebrew/Caskroom/claude-code -maxdepth 2 -name claude -type f 2>/dev/null | sort -V | tail -1)
fi
if [ -z "$REAL_CLAUDE" ] || [ ! -f "$REAL_CLAUDE" ]; then
    FOUND_ON_PATH=$(command -v claude || true)
    if [ -n "$FOUND_ON_PATH" ]; then
        RESOLVED_PATH=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$FOUND_ON_PATH")
        case "$RESOLVED_PATH" in
            "$INSTALL_DIR"/*|"$SOURCE_DIR"/*) ;;
            *) REAL_CLAUDE="$RESOLVED_PATH" ;;
        esac
    fi
fi
if [ -z "$REAL_CLAUDE" ] || [ ! -f "$REAL_CLAUDE" ]; then
    warn "Could not find the real 'claude' CLI. If you use Claude Code, run later:"
    warn "    claudeguard set-cli-path /path/to/real/claude"
    REAL_CLAUDE="/opt/homebrew/bin/claude"
else
    ok "Real Claude CLI: $REAL_CLAUDE"
fi

mkdir -p "$HOME/.config/claudeguard"
python3 -c "
import json, os
path = os.path.expanduser('~/.config/claudeguard/config.json')
data = {}
if os.path.exists(path):
    try:
        with open(path) as f: data = json.load(f)
    except Exception: pass
data['real_claude_cli_path'] = '$REAL_CLAUDE'
with open(path, 'w') as f: json.dump(data, f, indent=2)
"

# --- 5. Symlink the CLIs (manager + `claude` interceptor) -------------------
info "Installing the 'claudeguard' manager + 'claude' interceptor…"
mkdir -p "$HOME/.local/bin"
ln -sf "$INSTALL_DIR/bin/claudeguard" "$HOME/.local/bin/claudeguard"
[ -w "/usr/local/bin" ] && ln -sf "$INSTALL_DIR/bin/claudeguard" "/usr/local/bin/claudeguard"

ln -sf "$INSTALL_DIR/src/cli_wrapper.py" "$HOME/.local/bin/claude"
[ -w "/opt/homebrew/bin" ] && ln -sf "$INSTALL_DIR/src/cli_wrapper.py" "/opt/homebrew/bin/claude"
[ -w "/usr/local/bin" ]    && ln -sf "$INSTALL_DIR/src/cli_wrapper.py" "/usr/local/bin/claude"

# --- 6. LaunchAgent (autostart at login) ------------------------------------
info "Registering login-autostart LaunchAgent…"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.claudeguard.daemon</string>
    <key>ProgramArguments</key>
    <array><string>$INSTALL_DIR/bin/ClaudeGuardMenuBar</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
    <key>StandardOutPath</key><string>/tmp/claudeguard.log</string>
    <key>StandardErrorPath</key><string>/tmp/claudeguard.err</string>
</dict>
</plist>
EOF
pkill -f "ClaudeGuardMenuBar" 2>/dev/null || true
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH" 2>/dev/null || true

# --- 7. Passwordless network helper via scoped sudoers ----------------------
# hosts-helper is NOT setuid (chmod 750): root comes only from sudo, scoped to
# this exact path + this one user below.
info "Configuring the passwordless network helper (needs your password once)…"
chmod 750 "$INSTALL_DIR/bin/hosts-helper"
SUDOERS_BODY="$USER ALL=(ALL) NOPASSWD: /bin/cp /tmp/claudeguard_hosts.tmp /etc/hosts
$USER ALL=(ALL) NOPASSWD: /bin/chmod 644 /etc/hosts
$USER ALL=(ALL) NOPASSWD: $INSTALL_DIR/bin/hosts-helper"

if [ "$(id -u)" -eq 0 ]; then
    chmod 644 /etc/hosts 2>/dev/null || true
    printf '%s\n' "$SUDOERS_BODY" > "$SUDOERS_FILE"; chmod 440 "$SUDOERS_FILE"
else
    sudo chmod 644 /etc/hosts 2>/dev/null || true
    printf '%s\n' "$SUDOERS_BODY" | sudo tee "$SUDOERS_FILE" >/dev/null
    sudo chmod 440 "$SUDOERS_FILE"
fi
if have visudo; then
    if [ "$(id -u)" -eq 0 ]; then
        visudo -c -f "$SUDOERS_FILE" >/dev/null || { warn "sudoers fragment failed validation, removing."; rm -f "$SUDOERS_FILE"; }
    else
        sudo visudo -c -f "$SUDOERS_FILE" >/dev/null || { warn "sudoers fragment failed validation, removing."; sudo rm -f "$SUDOERS_FILE"; }
    fi
fi

# --- 8. First-run: offer to whitelist the current public IP -----------------
# Best done while you're connected to your VPN right now.
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

printf '\n%s\n' "${GRN}${BOLD}🎉 ClaudeGuard installed & running.${RST}"
printf '%s\n' "   Look for the shield in your macOS menu bar."
printf '%s\n' "   ${DIM}Manage it:${RST} ${BOLD}claudeguard status${RST}  ·  ${BOLD}claudeguard add-ip <IP>${RST}"
if ! printf '%s' "$PATH" | tr ':' '\n' | grep -qx "$HOME/.local/bin"; then
    printf '%s\n' "   ${YLW}Note:${RST} add ~/.local/bin to your PATH to use 'claudeguard' directly:"
    printf '%s\n' "         ${DIM}echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc${RST}"
fi
