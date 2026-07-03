from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.session import SessionLocal
from app.services.material_reanonymization import MaterialReanonymizationService


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview or re-run anonymization for all existing materials."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply anonymization updates. Without this flag only a dry run is shown.",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        service = MaterialReanonymizationService(session)
        if not args.execute:
            preview = service.preview()
            print("Existing materials anonymization preview:")
            print(f"  scanned: {preview.scanned}")
            print(f"  would_update: {preview.would_update}")
            print(f"  would_need_review: {preview.would_need_review}")
            print(f"  active_would_move_to_review: {preview.active_would_move_to_review}")
            print(f"  redactions: {preview.redactions}")
            print(f"  person_name_reviews: {preview.person_name_reviews}")
            print("Dry run only. Re-run with --execute to apply changes.")
            return 0

        result = service.execute()
        session.commit()
        print("Existing materials anonymization complete:")
        print(f"  scanned: {result.scanned}")
        print(f"  updated: {result.updated}")
        print(f"  needs_review: {result.needs_review}")
        print(f"  active_moved_to_review: {result.active_moved_to_review}")
        print(f"  redactions: {result.redactions}")
        print(f"  person_name_reviews: {result.person_name_reviews}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
