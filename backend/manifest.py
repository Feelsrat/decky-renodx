import os
import json
import shutil
from pathlib import Path
from datetime import datetime

class ManifestManager:
    def __init__(self, manifests_dir: str):
        self.manifests_dir = os.path.expanduser(manifests_dir)
        os.makedirs(self.manifests_dir, exist_ok=True)

    def _get_manifest_path(self, appid: str) -> str:
        return os.path.join(self.manifests_dir, f"{appid}.json")

    def write_manifest(self, appid: str, data: dict):
        """Write or update a manifest for a specific appid."""
        path = self._get_manifest_path(appid)
        
        # Add metadata
        data["last_updated"] = datetime.now().isoformat()
        
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return path

    def read_manifest(self, appid: str) -> dict or None:
        """Read a manifest for a specific appid."""
        path = self._get_manifest_path(appid)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return None

    def remove_hdr(self, appid: str, logger=None):
        """Surgically remove HDR based on the manifest."""
        manifest = self.read_manifest(appid)
        if not manifest:
            if logger: logger.warning(f"No manifest found for appid {appid}")
            return False, "No manifest found."

        errors = []
        files_removed = []
        backups_restored = []

        # 1. Restore backups
        for original, backup in manifest.get("backups", {}).items():
            if os.path.exists(backup):
                try:
                    shutil.move(backup, original)
                    backups_restored.append(original)
                    if logger: logger.info(f"Restored backup: {original}")
                except Exception as e:
                    errors.append(f"Failed to restore {original}: {str(e)}")
            else:
                if logger: logger.warning(f"Backup file not found for restoration: {backup}")

        # 2. Delete installed files
        for file_path in manifest.get("installed_files", []):
            if os.path.exists(file_path):
                try:
                    if os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                    else:
                        os.remove(file_path)
                    files_removed.append(file_path)
                    if logger: logger.info(f"Removed file: {file_path}")
                except Exception as e:
                    errors.append(f"Failed to remove {file_path}: {str(e)}")

        # 3. Clean up empty directories? (Optional, maybe skip for safety)

        # 4. Delete the manifest itself if successful
        if not errors:
            try:
                os.remove(self._get_manifest_path(appid))
            except Exception:
                pass
            return True, "Successfully removed HDR."
        else:
            return False, "; ".join(errors)
