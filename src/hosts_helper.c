#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <errno.h>
#include <string.h>
#include <ctype.h>

/*
 * Privileged helper, run by systemd as root (claudeguard-helper.service)
 * whenever the user daemon touches the trigger file via systemd path unit.
 *
 *   argv[1] = the ClaudeGuard config dir (~/.config/claudeguard)
 *
 * The staged files live in <config-dir>/pending and are written by an
 * unprivileged user, so as root we must not trust them blindly: the config
 * dir's owner is the only uid we accept, and each source file is opened
 * O_NOFOLLOW and checked to be a regular file owned by that uid before use.
 */

#define CHAIN_NAME "CLAUDEGUARD"
#define MAX_LINE 256

static int open_trusted_regular_file(const char *path, uid_t owner) {
    int fd = open(path, O_RDONLY | O_NOFOLLOW);
    if (fd < 0) {
        return -1;
    }
    struct stat st;
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_uid != owner) {
        close(fd);
        errno = EPERM;
        return -1;
    }
    return fd;
}

static int copy_fd_to_path(int src_fd, const char *dst_path, mode_t mode) {
    int dst_fd = open(dst_path, O_WRONLY | O_CREAT | O_TRUNC, mode);
    if (dst_fd < 0) {
        return -1;
    }
    char buf[65536];
    ssize_t n;
    int ok = 1;
    while ((n = read(src_fd, buf, sizeof(buf))) > 0) {
        ssize_t off = 0;
        while (off < n) {
            ssize_t w = write(dst_fd, buf + off, (size_t)(n - off));
            if (w < 0) { ok = 0; break; }
            off += w;
        }
        if (!ok) break;
    }
    if (n < 0) ok = 0;
    if (fchmod(dst_fd, mode) != 0) ok = 0;
    close(dst_fd);
    return ok ? 0 : -1;
}

/* Validate that a string looks like an IPv4 or IPv6 address (no shell injection). */
static int is_valid_ip(const char *s) {
    if (!s || !*s) return 0;
    size_t len = strlen(s);
    if (len > 45) return 0;
    for (const char *p = s; *p; p++) {
        char c = *p;
        if (!isdigit(c) && c != '.' && c != ':' &&
            !(c >= 'a' && c <= 'f') && !(c >= 'A' && c <= 'F'))
            return 0;
    }
    return 1;
}

static void flush_dns(void) {
    if (system("resolvectl flush-caches 2>/dev/null") != 0) {
        if (system("systemd-resolve --flush-caches 2>/dev/null") != 0) {
            system("systemctl restart nscd 2>/dev/null");
        }
    }
}

static void apply_iptables(const char *config_dir, uid_t owner) {
    char fw_path[2048];
    snprintf(fw_path, sizeof(fw_path), "%s/pending/fw.ips", config_dir);

    struct stat st;
    int has_fw = (lstat(fw_path, &st) == 0);

    char cmd[4096];

    /* Ensure chain exists */
    snprintf(cmd, sizeof(cmd),
             "iptables -N %s 2>/dev/null; ip6tables -N %s 2>/dev/null",
             CHAIN_NAME, CHAIN_NAME);
    system(cmd);

    /* Flush existing rules */
    snprintf(cmd, sizeof(cmd),
             "iptables -F %s 2>/dev/null; ip6tables -F %s 2>/dev/null",
             CHAIN_NAME, CHAIN_NAME);
    system(cmd);

    if (has_fw) {
        int fd = open_trusted_regular_file(fw_path, owner);
        if (fd < 0) {
            fprintf(stderr, "hosts-helper: refusing untrusted %s: %s\n",
                    fw_path, strerror(errno));
            return;
        }

        FILE *f = fdopen(fd, "r");
        if (!f) { close(fd); return; }

        char line[MAX_LINE];
        while (fgets(line, sizeof(line), f)) {
            char *start = line;
            while (*start && isspace(*start)) start++;
            char *end = start + strlen(start) - 1;
            while (end > start && isspace(*end)) *end-- = '\0';

            if (!*start || *start == '#') continue;
            if (!is_valid_ip(start)) continue;

            if (strchr(start, ':')) {
                snprintf(cmd, sizeof(cmd),
                         "ip6tables -A %s -d %s -j DROP", CHAIN_NAME, start);
            } else {
                snprintf(cmd, sizeof(cmd),
                         "iptables -A %s -d %s -j DROP", CHAIN_NAME, start);
            }
            system(cmd);
        }
        fclose(f);

        /* Hook into OUTPUT if not already there */
        snprintf(cmd, sizeof(cmd),
                 "iptables -C OUTPUT -j %s 2>/dev/null || iptables -I OUTPUT -j %s",
                 CHAIN_NAME, CHAIN_NAME);
        system(cmd);
        snprintf(cmd, sizeof(cmd),
                 "ip6tables -C OUTPUT -j %s 2>/dev/null || ip6tables -I OUTPUT -j %s",
                 CHAIN_NAME, CHAIN_NAME);
        system(cmd);
    } else {
        /* Unblock: remove chain from OUTPUT and delete it */
        snprintf(cmd, sizeof(cmd),
                 "iptables -D OUTPUT -j %s 2>/dev/null; "
                 "ip6tables -D OUTPUT -j %s 2>/dev/null",
                 CHAIN_NAME, CHAIN_NAME);
        system(cmd);
        snprintf(cmd, sizeof(cmd),
                 "iptables -X %s 2>/dev/null; ip6tables -X %s 2>/dev/null",
                 CHAIN_NAME, CHAIN_NAME);
        system(cmd);
    }
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: hosts-helper <config-dir>\n");
        return 2;
    }
    const char *config_dir = argv[1];

    struct stat cst;
    if (stat(config_dir, &cst) != 0 || !S_ISDIR(cst.st_mode)) {
        fprintf(stderr, "hosts-helper: bad config dir %s: %s\n",
                config_dir, strerror(errno));
        return 2;
    }
    uid_t owner = cst.st_uid;

    char hosts_tmp[2048];
    snprintf(hosts_tmp, sizeof(hosts_tmp), "%s/pending/hosts.tmp", config_dir);

    /* 1. Sync /etc/hosts from the vetted staged file. */
    int hosts_src = open_trusted_regular_file(hosts_tmp, owner);
    if (hosts_src >= 0) {
        if (copy_fd_to_path(hosts_src, "/etc/hosts", 0644) != 0) {
            fprintf(stderr, "hosts-helper: failed to write /etc/hosts: %s\n",
                    strerror(errno));
        }
        close(hosts_src);
    } else {
        fprintf(stderr, "hosts-helper: refusing untrusted %s: %s\n",
                hosts_tmp, strerror(errno));
    }

    flush_dns();

    /* 2. Apply or clear iptables rules. */
    apply_iptables(config_dir, owner);

    return 0;
}
