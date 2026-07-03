from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.session import SessionLocal
from app.search import SearchService
from app.services.question_linking import QuestionLinkRebuildService


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview or rebuild resident question links from a saved Telegram JSON export."
    )
    parser.add_argument("--batch-id", type=int, required=True, help="Import batch id to rebuild.")
    parser.add_argument("--export-path", type=Path, help="Override Telegram JSON export path.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply updates. Without this flag only a dry run is shown.",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        result = QuestionLinkRebuildService(session).rebuild_for_batch(
            batch_id=args.batch_id,
            export_path=args.export_path,
            execute=args.execute,
        )
        if args.execute:
            SearchService(session).rebuild_index()
            session.commit()
            print("Question links rebuild complete:")
        else:
            print("Question links rebuild preview:")
        print(f"  scanned: {result.scanned}")
        print(f"  questions_existing: {result.questions_existing}")
        print(f"  questions_created: {result.questions_created}")
        print(f"  links_existing: {result.links_existing}")
        print(f"  links_created: {result.links_created}")
        if not args.execute:
            print("Dry run only. Re-run with --execute to apply changes.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
