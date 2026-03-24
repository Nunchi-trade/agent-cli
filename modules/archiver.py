"""State file archiver — moves closed position state to archive on close."""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("archiver")


class StateArchiver:
    """Archives closed position state files to data/archive/{date}/."""

    def __init__(self, archive_dir: str = "data/archive"):
        self.archive_dir = Path(archive_dir)

    def archive_guard_state(self, guard_dir: str, position_id: str) -> bool:
        """Move closed GUARD state file to archive. Returns True if moved."""
        src = Path(guard_dir) / f"{position_id}.json"
        if not src.exists():
            return False
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dest_dir = self.archive_dir / date_str / "guard"
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest_dir / src.name))
        return True

    def archive_slot_snapshot(self, slot_data: Dict[str, Any], slot_id: int) -> None:
        """Write closed slot data to archive."""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dest_dir = self.archive_dir / date_str / "apex"
        dest_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        dest = dest_dir / f"slot-{slot_id}-{ts}.json"
        dest.write_text(json.dumps(slot_data, indent=2, default=str))

    def archive_old(self, guard_dir: str, days_old: int = 0, dry_run: bool = False) -> Dict[str, int]:
        """Archive all closed GUARD state files. Returns counts by type."""
        counts = {"guard": 0, "skipped": 0}
        guard_path = Path(guard_dir)
        if not guard_path.exists():
            return counts

        now = time.time()
        cutoff = now - (days_old * 86400) if days_old > 0 else now + 86400  # future = archive all

        for f in guard_path.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                state = data.get("state", data)
                if not state.get("closed", False):
                    counts["skipped"] += 1
                    continue
                if f.stat().st_mtime > cutoff and days_old > 0:
                    counts["skipped"] += 1
                    continue
                if not dry_run:
                    self.archive_guard_state(str(guard_path), f.stem)
                counts["guard"] += 1
            except (json.JSONDecodeError, IOError) as e:
                log.warning("Skipping corrupted state file %s: %s", f.name, e)
                counts["skipped"] += 1

        return counts
