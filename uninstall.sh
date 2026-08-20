#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$HOME/.local/share/ClaudeGuard"
CONFIG_DIR="$HOME/.config/claudeguard"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
HELPER_SYS_DIR="/var/lib/claudeguard"

REAL_CLAUDE_TARGET=$(python3 -c "
import json, os
path = os.path.expanduser('~/.config/claudeguard/config.json')
try:
    with open(path) as f:
        print(json.load(f).get('real_claude_cli_path', ''))
except Exception:
    print('')
" 2>/dev/null || true)

echo "=================================================="
echo "  Uninstalling ClaudeGuard (Clean Reset)"
echo "=================================================="

# 1. Stop and disable systemd user service
echo "--> Stopping systemd user service..."
systemctl --user stop claudeguard.service 2>/dev/null || true
systemctl --user disable claudeguard.service 2>/dev/null || true
rm -f "$SYSTEMD_USER_DIR/claudeguard.service"
systemctl --user daemon-reload 2>/dev/null || true

# 2. Terminate any remaining daemon processes
echo "--> Stopping running processes..."
pkill -f "daemon.py.*ClaudeGuard" 2>/dev/null || true

# 3. Remove the root network-helper (systemd path + service), clear iptables
echo "--> Removing root network helper..."
sudo systemctl stop claudeguard-helper.path 2>/dev/null || true
sudo systemctl disable claudeguard-helper.path 2>/dev/null || true
sudo rm -f /etc/systemd/system/claudeguard-helper.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/claudeguard-helper.path 2>/dev/null || true
sudo systemctl daemon-reload 2>/dev/null || true
sudo rm -rf "$HELPER_SYS_DIR" 2>/dev/null || true

# Clear iptables rules
echo "--> Clearing iptables rules..."
for prog in iptables ip6tables; do
    sudo $prog -D OUTPUT -j CLAUDEGUARD 2>/dev/null || true
    sudo $prog -F CLAUDEGUARD 2>/dev/null || true
    sudo $prog -X CLAUDEGUARD 2>/dev/null || true
done

# 4. Restore /etc/hosts file
echo "--> Restoring /etc/hosts..."
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
        subprocess.run(['sudo', 'cp', tmp, HOSTS_PATH], check=False)
        os.remove(tmp)
    except: pass
" 2>/dev/null || true

# Flush DNS
echo "--> Flushing DNS cache..."
resolvectl flush-caches 2>/dev/null \
    || systemd-resolve --flush-caches 2>/dev/null \
    || systemctl restart nscd 2>/dev/null \
    || true

# 5. Unlock auto-update paths
echo "--> Unlocking filesystem auto-update locks..."
for target in "$HOME/.config/claude-desktop/app-update.yml" \
              "$HOME/.config/Claude/app-update.yml" \
              "$HOME/.cache/claude-desktop" \
              "$HOME/.cache/Claude"; do
    [ -e "$target" ] || continue
    sudo chattr -R -i "$target" 2>/dev/null || true
    if [ -d "$target" ]; then
        chmod -R 755 "$target" 2>/dev/null || true
    else
        chmod 644 "$target" 2>/dev/null || true
    fi
done

# 6. Un-hook 'claude' and restore the real binary
echo "--> Restoring original 'claude' CLI binary..."
rm -f "$HOME/.local/bin/claudeguard"
sudo rm -f "/usr/local/bin/claudeguard" 2>/dev/null || true

for link in "$HOME/.local/bin/claude" "/usr/local/bin/claude"; do
    [ -L "$link" ] || continue
    case "$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$link" 2>/dev/null)" in
        *cli_wrapper.py) rm -f "$link" && echo "    Removed ClaudeGuard shim: $link" ;;
    esac
done

if [ -n "$REAL_CLAUDE_TARGET" ] && [ -f "$REAL_CLAUDE_TARGET" ]; then
    RESTORE_DIR="$HOME/.local/bin"
    if [ -d "$RESTORE_DIR" ] && [ -w "$RESTORE_DIR" ] && [ ! -e "$RESTORE_DIR/claude" ]; then
        ln -sf "$REAL_CLAUDE_TARGET" "$RESTORE_DIR/claude"
        echo "    Restored $RESTORE_DIR/claude -> $REAL_CLAUDE_TARGET"
    fi
else
    echo "    Could not determine the original claude CLI path; nothing restored."
fi

# 7. Remove installed files and configs
echo "--> Cleaning configuration and installation files..."
rm -rf "$INSTALL_DIR"
rm -rf "$CONFIG_DIR"

echo ""
echo "=================================================="
echo "  ClaudeGuard completely uninstalled!"
echo "  System restored to clean state."
echo "=================================================="
