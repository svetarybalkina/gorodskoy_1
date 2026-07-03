from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.import_cleanup import cleanup_test_import_materials, preview_test_import_cleanup


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or delete imported draft/review/duplicate materials before the full acceptance import."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete the selected imported materials. Without this flag only a dry run is shown.",
    )
    parser.add_argument(
        "--source-id",
        help="Official Telegram source external id. Defaults to OFFICIAL_TELEGRAM_SOURCE_ID from .env.",
    )
    parser.add_argument(
        "--all-sources",
        action="store_true",
        help="Clean imported draft/review/duplicate materials from all sources.",
    )
    args = parser.parse_args()

    settings = get_settings()
    source_id = args.source_id or settings.official_telegram_source_id

    with SessionLocal() as session:
        preview = preview_test_import_cleanup(
            session,
            source_external_id=source_id,
            all_sources=args.all_sources,
        )
        print("Selected imported materials:")
        print(f"  materials: {preview.materials}")
        print(f"  admin_notes: {preview.admin_notes}")
        print(f"  question_variants: {preview.question_variants}")
        print(f"  material_links: {preview.material_links}")
        print(f"  redaction_events: {preview.redaction_events}")
        print(f"  person_name_reviews: {preview.person_name_reviews}")
        print(f"  dictionary_candidates: {preview.dictionary_candidates}")
        print(f"  material_recommendations: {preview.material_recommendations}")
        print(f"  problem_queries_to_unlink: {preview.problem_queries_to_unlink}")

        if not args.execute:
            print("Dry run only. Re-run with --execute to apply cleanup.")
            return 0

        result = cleanup_test_import_materials(
            session,
            source_external_id=source_id,
            all_sources=args.all_sources,
        )
        session.commit()
        print(f"Cleanup complete. Deleted materials: {result.materials}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
