#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <errno.h>
#include <string.h>

/*
 * Privileged helper invoked as `sudo hosts-helper` (scoped sudoers NOPASSWD
 * entry, installed by install.sh). Not setuid — root comes from sudo alone.
 *
 * The /tmp source files are world-writable, so as root we must not follow a
 * symlink or trust a file planted by another user: each is opened O_NOFOLLOW
 * and its owner checked against the invoking user ($SUDO_UID) before use.
 */

static uid_t invoking_uid(void) {
    const char *sudo_uid = getenv("SUDO_UID");
    if (sudo_uid && *sudo_uid) {
        return (uid_t)strtoul(sudo_uid, NULL, 10);
    }
    return getuid();
}

/* Opens `path` safely: must be a regular file, not a symlink, owned by the
 * invoking user. Returns an open fd on success, or -1 on failure. */
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

int main(void) {
    uid_t owner = invoking_uid();

    /* 1. Sync /etc/hosts from the vetted temp file. */
    int hosts_src = open_trusted_regular_file("/tmp/claudeguard_hosts.tmp", owner);
    if (hosts_src >= 0) {
        if (copy_fd_to_path(hosts_src, "/etc/hosts", 0644) != 0) {
            fprintf(stderr, "hosts-helper: failed to write /etc/hosts: %s\n", strerror(errno));
        }
        close(hosts_src);
        unlink("/tmp/claudeguard_hosts.tmp");
    } else {
        fprintf(stderr, "hosts-helper: refusing untrusted /tmp/claudeguard_hosts.tmp: %s\n", strerror(errno));
    }

    system("/usr/bin/dscacheutil -flushcache");
    system("/usr/bin/killall -HUP mDNSResponder");

    /* 2. Load or clear the pf rule, from the vetted temp file only. */
    struct stat pf_st;
    if (lstat("/tmp/claudeguard_pf.rule", &pf_st) == 0) {
        int pf_src = open_trusted_regular_file("/tmp/claudeguard_pf.rule", owner);
        if (pf_src >= 0) {
            if (copy_fd_to_path(pf_src, "/tmp/claudeguard_pf.rule.verified", 0600) == 0) {
                system("/sbin/pfctl -e -f /tmp/claudeguard_pf.rule.verified 2>/dev/null");
                system("/sbin/pfctl -F states 2>/dev/null");
                unlink("/tmp/claudeguard_pf.rule.verified");
            }
            close(pf_src);
        } else {
            fprintf(stderr, "hosts-helper: refusing untrusted /tmp/claudeguard_pf.rule: %s\n", strerror(errno));
        }
    } else {
        system("/sbin/pfctl -d 2>/dev/null");
        system("/sbin/pfctl -F states 2>/dev/null");
    }

    return 0;
}
