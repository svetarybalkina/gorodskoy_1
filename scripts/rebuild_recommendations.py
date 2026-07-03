from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.enums import MaterialStatus
from app.db.session import SessionLocal
from app.search import SearchService
from app.services.recommendations import RecommendationExtractionService


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview or rebuild extracted recommendations for existing materials."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply recommendation updates. Without this flag only a dry run is shown.",
    )
    parser.add_argument(
        "--status",
        choices=[status.value for status in MaterialStatus],
        help="Only process materials with this status.",
    )
    parser.add_argument(
        "--public-only",
        action="store_true",
        help="Only process active official materials from public topics.",
    )
    parser.add_argument("--limit", type=int, help="Only process the first N matching materials.")
    args = parser.parse_args()

    status = MaterialStatus(args.status) if args.status else None

    with SessionLocal() as session:
        service = RecommendationExtractionService(session)
        result = service.rebuild(
            status=status,
            public_only=args.public_only,
            execute=args.execute,
            limit=args.limit,
        )
        if args.execute:
            SearchService(session).rebuild_index()
            session.commit()
            print("Material recommendations rebuild complete:")
            print(f"  scanned: {result.scanned}")
            print(f"  changed: {result.changed}")
            print(f"  recommendations: {result.recommendations}")
        else:
            print("Material recommendations rebuild preview:")
            print(f"  scanned: {result.scanned}")
            print(f"  would_change: {result.would_change}")
            print(f"  recommendations: {result.recommendations}")
            print("Dry run only. Re-run with --execute to apply changes.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
