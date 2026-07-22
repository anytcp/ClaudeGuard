import os
import subprocess

SHIPIT_DIR = os.path.expanduser("~/Library/Caches/com.anthropic.claudefor-mac.ShipIt")
UPDATE_YML = os.path.expanduser("~/Library/Application Support/Claude/app-update.yml")

def apply_auto_update_lock(block_updates):
    """Lock/unlock Claude Desktop's auto-update dirs (chmod + chflags uchg)."""
    try:
        if block_updates:
            if os.path.exists(SHIPIT_DIR):
                subprocess.run(["chmod", "-R", "444", SHIPIT_DIR], check=False)
                subprocess.run(["chflags", "-R", "uchg", SHIPIT_DIR], check=False)
            if os.path.exists(UPDATE_YML):
                subprocess.run(["chmod", "444", UPDATE_YML], check=False)
                subprocess.run(["chflags", "uchg", UPDATE_YML], check=False)
        else:
            if os.path.exists(SHIPIT_DIR):
                subprocess.run(["chflags", "-R", "nouchg", SHIPIT_DIR], check=False)
                subprocess.run(["chmod", "-R", "755", SHIPIT_DIR], check=False)
            if os.path.exists(UPDATE_YML):
                subprocess.run(["chflags", "nouchg", UPDATE_YML], check=False)
                subprocess.run(["chmod", "644", UPDATE_YML], check=False)

        return True, "Auto-update lock status updated"
    except Exception as e:
        return False, f"Failed to modify auto-update locks: {e}"
