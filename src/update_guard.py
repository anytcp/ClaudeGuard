import os
import subprocess

from src.integrity import desktop_update_paths


def apply_auto_update_lock(block_updates):
    """Lock/unlock Claude Desktop's auto-update state (chmod + chattr on Linux).

    Paths are discovered, not hard-coded: they're keyed on a bundle id that has
    already been renamed once, and a stale one doesn't error — it silently
    matches nothing, so the freeze stops freezing while the UI still says it's
    on."""
    try:
        targets = desktop_update_paths()
        if not targets:
            return True, "No Claude Desktop update state found (nothing to lock)"

        for path in targets:
            recursive = ["-R"] if os.path.isdir(path) else []
            if block_updates:
                subprocess.run(["chmod"] + recursive + ["444", path], check=False)
                subprocess.run(["chattr"] + recursive + ["+i", path],
                               check=False, stderr=subprocess.DEVNULL)
            else:
                subprocess.run(["chattr"] + recursive + ["-i", path],
                               check=False, stderr=subprocess.DEVNULL)
                subprocess.run(["chmod"] + recursive + ["755" if os.path.isdir(path) else "644", path],
                               check=False)

        return True, f"Auto-update lock applied to {len(targets)} path(s)"
    except Exception as e:
        return False, f"Failed to modify auto-update locks: {e}"
