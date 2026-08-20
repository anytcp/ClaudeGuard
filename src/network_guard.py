import ipaddress
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HOSTS_PATH = "/etc/hosts"
BLOCK_HEADER = "# BEGIN CLAUDEGUARD BLOCKS\n"
BLOCK_FOOTER = "# END CLAUDEGUARD BLOCKS\n"

UPDATE_HEADER = "# BEGIN CLAUDEGUARD UPDATE BLOCKS\n"
UPDATE_FOOTER = "# END CLAUDEGUARD UPDATE BLOCKS\n"

CONFIG_DIR = os.path.expanduser("~/.config/claudeguard")
PENDING_DIR = os.path.join(CONFIG_DIR, "pending")
PENDING_HOSTS = os.path.join(PENDING_DIR, "hosts.tmp")
FW_IPS_PATH = os.path.join(PENDING_DIR, "fw.ips")
REQUEST_FILE = os.path.join(PENDING_DIR, "request")

IPTABLES_CHAIN = "CLAUDEGUARD"

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

EXTERNAL_RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]

ANTHROPIC_NETS = [
    ipaddress.ip_network("160.79.104.0/24"),
    ipaddress.ip_network("2607:6bc0::/32"),
]

FALLBACK_IPS = ["160.79.104.10", "2607:6bc0::10"]

PF_RESOLVE_DOMAINS = [
    "claude.ai", "www.claude.ai", "app.claude.ai", "api.claude.ai", "auth.claude.ai",
    "claude.com", "www.claude.com", "app.claude.com", "api.claude.com", "console.claude.com",
    "anthropic.com", "www.anthropic.com", "api.anthropic.com", "console.anthropic.com",
]

def _is_ip(text):
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(family, text)
            return True
        except OSError:
            pass
    return False

def _in_anthropic_range(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in ANTHROPIC_NETS)

def _find_dig():
    for path in ("/usr/bin/dig", "/usr/sbin/dig"):
        if os.path.exists(path):
            return path
    return "dig"

def _dig(resolver, record_type, domain):
    dig_bin = _find_dig()
    try:
        res = subprocess.run(
            [dig_bin, f"@{resolver}", "+short", "+time=1", "+tries=1", record_type, domain],
            capture_output=True, text=True, timeout=3
        )
    except Exception:
        return False, []
    if res.returncode != 0:
        return False, []
    ips = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if line and not line.endswith(".") and _is_ip(line):
            ips.append(line)
    return True, ips

def _resolve_one(domain):
    ips = set()
    for record_type in ("A", "AAAA"):
        for resolver in EXTERNAL_RESOLVERS:
            ok, found = _dig(resolver, record_type, domain)
            if ok:
                ips.update(found)
                break
    return ips

def resolve_ips(domains):
    domains = list(domains)
    if not domains:
        return set()
    ips = set()
    with ThreadPoolExecutor(max_workers=min(16, len(domains))) as pool:
        for found in pool.map(_resolve_one, domains):
            ips.update(found)
    return ips

def resolve_domain_ips(domains):
    ips = {ip for ip in resolve_ips(domains) if _in_anthropic_range(ip)}
    ips.update(FALLBACK_IPS)
    return sorted(ips)

def generate_all_subdomains():
    domains = set(KNOWN_RESOLVED_DOMAINS)
    for apex in APEX_DOMAINS:
        domains.add(apex)
        for sub in SUBDOMAIN_PREFIXES:
            if sub:
                domains.add(f"{sub}.{apex}")
    return sorted(list(domains))

_cached_fw_ips = set()

def sync_hosts_file(block_claude_domains, block_update_domains, config,
                    skip_dns=False):
    """Block/unblock all Claude & Anthropic subdomains via /etc/hosts and
    iptables (TCP + UDP).

    skip_dns: when True (offline), skip live DNS resolution for iptables IPs
    and use cached + fallback IPs only. This avoids a 9+ second timeout
    that would delay enforcement when the network is down."""
    global _cached_fw_ips
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

        _ensure_pending()

        if block_claude_domains:
            if skip_dns:
                ip_set = set(FALLBACK_IPS)
                ip_set.update(_cached_fw_ips)
            else:
                ip_set = set(resolve_domain_ips(PF_RESOLVE_DOMAINS))
                custom = config.config.get("blocked_domains", [])
                if custom:
                    ip_set.update(resolve_ips(custom))
                _cached_fw_ips = set(ip_set)
            with open(FW_IPS_PATH, "w", encoding="utf-8") as f:
                for ip in sorted(ip_set):
                    f.write(ip + "\n")
        else:
            if os.path.exists(FW_IPS_PATH):
                try:
                    os.remove(FW_IPS_PATH)
                except Exception:
                    pass

        write_hosts(final_content)

        return True, "Hosts and iptables synced successfully"
    except Exception as e:
        return False, f"Failed to sync hosts: {e}"

def remove_section(text, header, footer):
    while header in text and footer in text:
        start = text.find(header)
        end = text.find(footer) + len(footer)
        text = text[:start] + text[end:]
    return text

def _ensure_pending():
    os.makedirs(PENDING_DIR, exist_ok=True)
    try:
        os.chmod(PENDING_DIR, 0o700)
    except OSError:
        pass

def _atomic_write(path, content):
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

def _flush_dns():
    for cmd in [
        "resolvectl flush-caches",
        "systemd-resolve --flush-caches",
        "systemctl restart nscd",
    ]:
        try:
            subprocess.run(cmd.split(), check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            continue

def _apply_iptables_block(ips):
    """Apply iptables DROP rules for the given IPs."""
    for prog in ("iptables", "ip6tables"):
        subprocess.run([prog, "-N", IPTABLES_CHAIN],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([prog, "-F", IPTABLES_CHAIN],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for ip in ips:
        prog = "ip6tables" if ":" in ip else "iptables"
        subprocess.run([prog, "-A", IPTABLES_CHAIN, "-d", ip, "-j", "DROP"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for prog in ("iptables", "ip6tables"):
        check = subprocess.run([prog, "-C", "OUTPUT", "-j", IPTABLES_CHAIN],
                               capture_output=True)
        if check.returncode != 0:
            subprocess.run([prog, "-I", "OUTPUT", "-j", IPTABLES_CHAIN],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _clear_iptables():
    for prog in ("iptables", "ip6tables"):
        subprocess.run([prog, "-D", "OUTPUT", "-j", IPTABLES_CHAIN],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([prog, "-F", IPTABLES_CHAIN],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([prog, "-X", IPTABLES_CHAIN],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _apply_as_root():
    """Direct application when we already are root."""
    subprocess.run(f"cp '{PENDING_HOSTS}' {HOSTS_PATH} && chmod 644 {HOSTS_PATH}",
                   shell=True, check=False)
    _flush_dns()
    if os.path.exists(FW_IPS_PATH):
        ips = []
        with open(FW_IPS_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    ips.append(line)
        _apply_iptables_block(ips)
    else:
        _clear_iptables()

def write_hosts(content):
    """Stage the desired /etc/hosts and signal the root helper to apply it."""
    _ensure_pending()
    _atomic_write(PENDING_HOSTS, content)

    if os.geteuid() == 0:
        _apply_as_root()
        return

    with open(REQUEST_FILE, "w", encoding="utf-8") as f:
        f.write(str(time.time()))
        f.flush()
        os.fsync(f.fileno())

    want_block = BLOCK_HEADER in content
    for _ in range(30):
        try:
            with open(HOSTS_PATH, "r", encoding="utf-8") as f:
                if (BLOCK_HEADER in f.read()) == want_block:
                    return
        except OSError:
            pass
        time.sleep(0.1)
