from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.enums import MaterialStatus
from app.db.seed import seed_initial_data
from app.db.session import SessionLocal
from app.services.material_reclassification import MaterialReclassificationService


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview or recalculate material topics and categories."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply topic/category updates. Without this flag only a dry run is shown.",
    )
    parser.add_argument(
        "--status",
        choices=[status.value for status in MaterialStatus],
        help="Only process materials with this status.",
    )
    parser.add_argument("--batch-id", type=int, help="Only process materials from this import batch.")
    parser.add_argument("--limit", type=int, help="Only process the first N matching materials.")
    args = parser.parse_args()

    status = MaterialStatus(args.status) if args.status else None

    with SessionLocal() as session:
        service = MaterialReclassificationService(session)
        if args.execute:
            seed_initial_data(session)
            result = service.execute(status=status, batch_id=args.batch_id, limit=args.limit)
            session.commit()
            print("Material reclassification complete:")
            print(f"  scanned: {result.scanned}")
            print(f"  changed: {result.changed}")
        else:
            result = service.preview(status=status, batch_id=args.batch_id, limit=args.limit)
            print("Material reclassification preview:")
            print(f"  scanned: {result.scanned}")
            print(f"  would_change: {result.would_change}")
            print("Dry run only. Re-run with --execute to apply changes.")
        if result.transitions:
            print("  transitions:")
            for transition in result.transitions:
                current = _label(transition.current_topic, transition.current_category)
                target = _label(transition.target_topic, transition.target_category)
                print(f"    {current} -> {target}: {transition.count}")
        return 0


def _label(topic: str, category: str | None) -> str:
    if category is None:
        return topic
    return f"{topic}/{category}"


if __name__ == "__main__":
    raise SystemExit(main())
