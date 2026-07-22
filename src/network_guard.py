import os
import subprocess
import sys

HOSTS_PATH = "/etc/hosts"
BLOCK_HEADER = "# BEGIN CLAUDEGUARD BLOCKS\n"
BLOCK_FOOTER = "# END CLAUDEGUARD BLOCKS\n"

UPDATE_HEADER = "# BEGIN CLAUDEGUARD UPDATE BLOCKS\n"
UPDATE_FOOTER = "# END CLAUDEGUARD UPDATE BLOCKS\n"

PF_RULE_PATH = "/tmp/claudeguard_pf.rule"

APEX_DOMAINS = [
    "claude.com",
    "claude.ai",
    "claude.usercontent.com",
    "anthropic.com",
    "anthropic.ai"
]

SUBDOMAIN_PREFIXES = [
    "", "www", "app", "api", "auth", "stats", "cdn", "assets",
    "console", "docs", "support", "feedback", "desktop-app-updates",
    "a-api", "a-cdn", "code", "status", "blog", "research", "events",
    "login", "register", "portal", "admin", "internal", "v1", "v2", "v3",
    "ws", "gateway", "proxy", "assets-proxy", "account", "settings", "billing"
]

KNOWN_RESOLVED_DOMAINS = [
    "claude.com", "www.claude.com", "console.claude.com", "docs.claude.com", "support.claude.com", "code.claude.com", "status.claude.com", "app.claude.com", "api.claude.com", "auth.claude.com",
    "claude.ai", "www.claude.ai", "app.claude.ai", "assets.claude.ai", "console.claude.ai", "a-cdn.claude.ai", "status.claude.ai", "api.claude.ai", "auth.claude.ai",
    "claude.usercontent.com", "www.claude.usercontent.com", "app.claude.usercontent.com",
    "anthropic.com", "www.anthropic.com", "api.anthropic.com", "assets.anthropic.com", "console.anthropic.com", "docs.anthropic.com", "support.anthropic.com", "feedback.anthropic.com", "desktop-app-updates.anthropic.com", "a-api.anthropic.com", "a-cdn.anthropic.com", "status.anthropic.com", "events.anthropic.com", "portal.anthropic.com", "assets-proxy.anthropic.com", "billing.anthropic.com",
    "anthropic.ai", "www.anthropic.ai", "app.anthropic.ai", "api.anthropic.ai", "auth.anthropic.ai"
]

def generate_all_subdomains():
    domains = set(KNOWN_RESOLVED_DOMAINS)
    for apex in APEX_DOMAINS:
        domains.add(apex)
        for sub in SUBDOMAIN_PREFIXES:
            if sub:
                domains.add(f"{sub}.{apex}")
    return sorted(list(domains))

def sync_hosts_file(block_claude_domains, block_update_domains, config):
    """Block/unblock all Claude & Anthropic subdomains via /etc/hosts and pf
    (TCP + UDP QUIC)."""
    try:
        if not os.path.exists(HOSTS_PATH):
            return False, "File /etc/hosts does not exist"

        with open(HOSTS_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        content = remove_section(content, BLOCK_HEADER, BLOCK_FOOTER)
        content = remove_section(content, UPDATE_HEADER, UPDATE_FOOTER)

        all_blocked = sorted(list(set(generate_all_subdomains() + config.config.get("blocked_domains", []))))

        new_blocks = ""
        if block_claude_domains:
            new_blocks += BLOCK_HEADER
            for domain in all_blocked:
                new_blocks += f"0.0.0.0\t{domain}\n"
                new_blocks += f"127.0.0.1\t{domain}\n"
                new_blocks += f"::1\t{domain}\n"
            new_blocks += BLOCK_FOOTER

        if block_update_domains:
            new_blocks += UPDATE_HEADER
            for domain in config.config.get("update_domains", []):
                new_blocks += f"0.0.0.0\t{domain}\n"
                new_blocks += f"127.0.0.1\t{domain}\n"
                new_blocks += f"::1\t{domain}\n"
            new_blocks += UPDATE_FOOTER

        final_content = content.rstrip() + "\n\n" + new_blocks if new_blocks else content.rstrip() + "\n"

        # pf rule blocks TCP + UDP (UDP covers HTTP/3 QUIC).
        if block_claude_domains:
            dom_space = " ".join(KNOWN_RESOLVED_DOMAINS)
            pf_content = f"table <claude_guard> persist {{ {dom_space} }}\nblock drop out proto {{ tcp, udp }} to <claude_guard> port {{ 80, 443 }}\n"
            with open(PF_RULE_PATH, "w", encoding="utf-8") as f:
                f.write(pf_content)
        else:
            if os.path.exists(PF_RULE_PATH):
                try:
                    os.remove(PF_RULE_PATH)
                except Exception:
                    pass

        write_hosts(final_content)

        return True, "Hosts and PF synced successfully"
    except Exception as e:
        return False, f"Failed to sync hosts: {e}"

def remove_section(text, header, footer):
    while header in text and footer in text:
        start = text.find(header)
        end = text.find(footer) + len(footer)
        text = text[:start] + text[end:]
    return text

def write_hosts(content):
    """Write /etc/hosts via the privileged helper (or direct when root)."""
    tmp_path = "/tmp/claudeguard_hosts.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)

    if os.geteuid() == 0:
        subprocess.run(f"cp {tmp_path} {HOSTS_PATH} && chmod 644 {HOSTS_PATH} && rm -f {tmp_path}", shell=True, check=False)
        if os.path.exists(PF_RULE_PATH):
            subprocess.run(f"pfctl -e -f {PF_RULE_PATH} && pfctl -F states", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            subprocess.run("pfctl -d && pfctl -F states", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return

    helper = os.path.expanduser("~/.local/share/ClaudeGuard/bin/hosts-helper")
    if os.path.exists(helper):
        res = subprocess.run(f"sudo {helper}", shell=True, capture_output=True, text=True)
        if res.returncode == 0:
            return

    cmd = f"sudo cp {tmp_path} {HOSTS_PATH} && sudo chmod 644 {HOSTS_PATH} && rm -f {tmp_path}"
    subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
