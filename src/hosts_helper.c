#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <errno.h>
#include <string.h>

/*
 * Privileged helper, run by launchd as root (LaunchDaemon
 * com.claudeguard.helper) whenever the user daemon touches the trigger file.
 * No sudo, no sudoers entry, no setuid — root comes from being a LaunchDaemon,
 * and the binary itself lives in a root-owned path so it can't be swapped.
 *
 *   argv[1] = the ClaudeGuard config dir (…/.config/claudeguard)
 *
 * The staged files live in <config-dir>/pending and are written by an
 * unprivileged user, so as root we must not trust them blindly: the config
 * dir's owner is the only uid we accept, and each source file is opened
 * O_NOFOLLOW and checked to be a regular file owned by that uid before use.
 */

#define HELPER_DIR "/Library/Application Support/ClaudeGuard"
#define VERIFIED_PF HELPER_DIR "/pf.verified"

/* Opens `path` safely: regular file, not a symlink, owned by `owner`.
 * Returns an open fd on success, or -1. */
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

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: hosts-helper <config-dir>\n");
        return 2;
    }
    const char *config_dir = argv[1];

    /* The config dir's owner is the only uid whose staged files we trust. */
    struct stat cst;
    if (stat(config_dir, &cst) != 0 || !S_ISDIR(cst.st_mode)) {
        fprintf(stderr, "hosts-helper: bad config dir %s: %s\n", config_dir, strerror(errno));
        return 2;
    }
    uid_t owner = cst.st_uid;

    char hosts_tmp[2048], pf_rule[2048];
    snprintf(hosts_tmp, sizeof(hosts_tmp), "%s/pending/hosts.tmp", config_dir);
    snprintf(pf_rule,   sizeof(pf_rule),   "%s/pending/pf.rule",   config_dir);

    /* 1. Sync /etc/hosts from the vetted staged file. */
    int hosts_src = open_trusted_regular_file(hosts_tmp, owner);
    if (hosts_src >= 0) {
        if (copy_fd_to_path(hosts_src, "/etc/hosts", 0644) != 0) {
            fprintf(stderr, "hosts-helper: failed to write /etc/hosts: %s\n", strerror(errno));
        }
        close(hosts_src);
    } else {
        fprintf(stderr, "hosts-helper: refusing untrusted %s: %s\n", hosts_tmp, strerror(errno));
    }

    system("/usr/bin/dscacheutil -flushcache");
    system("/usr/bin/killall -HUP mDNSResponder");

    /* 2. Load or clear the pf rule. pf.rule present => block; absent => unblock.
     * Copy to a root-owned verified path first (the staged file is in a
     * user-writable dir; don't hand it to pfctl directly). */
    struct stat pf_st;
    if (lstat(pf_rule, &pf_st) == 0) {
        int pf_src = open_trusted_regular_file(pf_rule, owner);
        if (pf_src >= 0) {
            mkdir(HELPER_DIR, 0755);
            if (copy_fd_to_path(pf_src, VERIFIED_PF, 0600) == 0) {
                system("/sbin/pfctl -e -f '" VERIFIED_PF "' 2>/dev/null");
                system("/sbin/pfctl -F states 2>/dev/null");
                unlink(VERIFIED_PF);
            }
            close(pf_src);
        } else {
            fprintf(stderr, "hosts-helper: refusing untrusted %s: %s\n", pf_rule, strerror(errno));
        }
    } else {
        system("/sbin/pfctl -d 2>/dev/null");
        system("/sbin/pfctl -F states 2>/dev/null");
    }

    return 0;
}
