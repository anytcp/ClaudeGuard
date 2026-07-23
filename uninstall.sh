#!/usr/bin/env bash

PLIST_PATH="$HOME/Library/LaunchAgents/com.claudeguard.daemon.plist"
SUDOERS_FILE="/etc/sudoers.d/claudeguard"
INSTALL_DIR="$HOME/.local/share/ClaudeGuard"
CONFIG_DIR="$HOME/.config/claudeguard"
# Real claude path recorded at install time (avoids hardcoding a version).
REAL_CLAUDE_TARGET=$(python3 -c "
import json, os
path = os.path.expanduser('~/.config/claudeguard/config.json')
try:
    with open(path) as f:
        print(json.load(f).get('real_claude_cli_path', ''))
except Exception:
    print('')
" 2>/dev/null)

echo "=================================================="
echo " 🛑 Uninstalling ClaudeGuard (Clean Reset)"
echo "=================================================="

# 1. Unload and delete LaunchAgent
echo "--> Unloading LaunchAgent..."
if [ -f "$PLIST_PATH" ]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "    Removed LaunchAgent: $PLIST_PATH"
fi

# 2. Terminate running background processes
echo "--> Stopping running processes..."
pkill -f "ClaudeGuardMenuBar" 2>/dev/null || true
pkill -f "ClaudeGuard" 2>/dev/null || true

# 2b. Remove the root network-helper LaunchDaemon + its binary, and disable pf
echo "--> Removing root network helper (LaunchDaemon)..."
HELPER_PLIST="/Library/LaunchDaemons/com.claudeguard.helper.plist"
HELPER_SYS_DIR="/Library/Application Support/ClaudeGuard"
sudo launchctl bootout system "$HELPER_PLIST" 2>/dev/null || sudo launchctl unload "$HELPER_PLIST" 2>/dev/null || true
sudo rm -f "$HELPER_PLIST" 2>/dev/null || true
sudo rm -rf "$HELPER_SYS_DIR" 2>/dev/null || true
sudo /sbin/pfctl -d 2>/dev/null || true
sudo /sbin/pfctl -F states 2>/dev/null || true

# 3. Restore /etc/hosts file
echo "--> Restoring /etc/hosts and DNS settings..."
python3 -c "
import os, subprocess
HOSTS_PATH = '/etc/hosts'
if os.path.exists(HOSTS_PATH):
    try:
        with open(HOSTS_PATH, 'r') as f: content = f.read()
        for header, footer in [
            ('# BEGIN CLAUDEGUARD BLOCKS\n', '# END CLAUDEGUARD BLOCKS\n'),
            ('# BEGIN CLAUDEGUARD UPDATE BLOCKS\n', '# END CLAUDEGUARD UPDATE BLOCKS\n')
        ]:
            while header in content and footer in content:
                start = content.find(header)
                end = content.find(footer) + len(footer)
                content = content[:start] + content[end:]
        tmp = '/tmp/claudeguard_clean_hosts.tmp'
        with open(tmp, 'w') as f: f.write(content)
        cmd = f'sudo cp {tmp} {HOSTS_PATH} && rm -f {tmp}'
        subprocess.run(cmd, shell=True, capture_output=True)
    except: pass
" 2>/dev/null || true

# Flush DNS
dscacheutil -flushcache 2>/dev/null || true
sudo killall -HUP mDNSResponder 2>/dev/null || true

# 4. Unlock auto-update folders
echo "--> Unlocking filesystem auto-update locks..."
SHIPIT_DIR="$HOME/Library/Caches/com.anthropic.claudefor-mac.ShipIt"
UPDATE_YML="$HOME/Library/Application Support/Claude/app-update.yml"
if [ -d "$SHIPIT_DIR" ]; then
    chflags -R nouchg "$SHIPIT_DIR" 2>/dev/null || true
    chmod -R 755 "$SHIPIT_DIR" 2>/dev/null || true
fi
if [ -f "$UPDATE_YML" ]; then
    chflags nouchg "$UPDATE_YML" 2>/dev/null || true
    chmod 644 "$UPDATE_YML" 2>/dev/null || true
fi

# 5. Restore original Homebrew 'claude' symlink
echo "--> Restoring original 'claude' CLI binary..."
rm -f "$HOME/.local/bin/claudeguard"
rm -f "$HOME/.local/bin/claude"
rm -f "/usr/local/bin/claudeguard" 2>/dev/null || true
rm -f "/usr/local/bin/claude" 2>/dev/null || true

if [ -n "$REAL_CLAUDE_TARGET" ] && [ -w "/opt/homebrew/bin" ] && [ -f "$REAL_CLAUDE_TARGET" ]; then
    ln -sf "$REAL_CLAUDE_TARGET" "/opt/homebrew/bin/claude"
    echo "    Restored /opt/homebrew/bin/claude -> $REAL_CLAUDE_TARGET"
elif [ -z "$REAL_CLAUDE_TARGET" ]; then
    echo "    ⚠️  Could not determine the original claude CLI path; leaving /opt/homebrew/bin/claude untouched."
    echo "       Reinstall Claude Code CLI (e.g. 'brew reinstall --cask claude-code') if 'claude' now points nowhere."
fi

# 6. Remove installed files and configs
echo "--> Cleaning configuration and installation files..."
rm -rf "$INSTALL_DIR"
rm -rf "$CONFIG_DIR"
sudo rm -f "$SUDOERS_FILE" 2>/dev/null || true

echo ""
echo "=================================================="
echo " ✨ ClaudeGuard completely uninstalled!"
echo " System restored to clean state."
echo "=================================================="
