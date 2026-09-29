"""One-time bridge: exports the tables the old Supabase-backed prototype
actually had data in (conversations, messages, projects — everything
else in the live schema was empty at migration time, per
docs/MIGRATION_AUDIT.md §10) and imports them into iV's new local
SqliteStorage. Single-user by design: profile_id is dropped on import,
matching core/conversations and core/projects' schema (see their module
docstrings).

The `supabase` package is no longer an iV dependency (see
docs/MIGRATION_AUDIT.md) — install it separately if you need the live-export
path below; the --from-json path needs nothing but the standard library.

    pip install supabase

Usage:
    export SUPABASE_URL=...
    export SUPABASE_KEY=...
    python3 scripts/migrate_supabase_to_sqlite.py --db-path iv.db

Or, if you already have an export as JSON files (id-preserving dicts,
one array per table) rather than live Supabase credentials:
    python3 scripts/migrate_supabase_to_sqlite.py --db-path iv.db \\
        --from-json path/to/export_dir
    # expects export_dir/{projects,conversations,messages}.json

This is a standalone script, not part of core/ — it exists to run once
per person migrating off Supabase, not as an ongoing dependency.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.storage.local import SqliteStorage  # noqa: E402


def export_from_supabase(supabase_url: str, supabase_key: str) -> dict[str, list[dict]]:
    from supabase import create_client

    client = create_client(supabase_url, supabase_key)
    return {
        "projects": client.table("projects").select("id,name,description,status,priority,created_at").execute().data,
        "conversations": client.table("conversations").select("id,title,created_at").execute().data,
        "messages": client.table("messages").select("id,conversation_id,role,content,model_used,created_at").execute().data,
    }


def load_from_json(export_dir: Path) -> dict[str, list[dict]]:
    return {
        name: json.loads((export_dir / f"{name}.json").read_text())
        for name in ("projects", "conversations", "messages")
    }


def import_into_sqlite(export: dict[str, list[dict]], db_path: str) -> dict[str, int]:
    storage = SqliteStorage(db_path)
    counts = {}
    try:
        for collection in ("projects", "conversations", "messages"):
            rows = export.get(collection, [])
            for row in rows:
                # id and created_at are preserved as-is (insert() only
                # fills them in if absent) — this keeps history's real
                # ordering and identity intact rather than reassigning
                # new ids/timestamps on import.
                storage.insert(collection, dict(row))
            counts[collection] = len(rows)
    finally:
        storage.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="iv.db", help="Local SQLite file to write into")
    parser.add_argument("--supabase-url", default=None, help="Overrides SUPABASE_URL env var")
    parser.add_argument("--supabase-key", default=None, help="Overrides SUPABASE_KEY env var")
    parser.add_argument("--from-json", default=None, help="Directory with projects/conversations/messages.json instead of a live Supabase call")
    args = parser.parse_args()

    if args.from_json:
        export = load_from_json(Path(args.from_json))
    else:
        import os
        url = args.supabase_url or os.environ["SUPABASE_URL"]
        key = args.supabase_key or os.environ["SUPABASE_KEY"]
        export = export_from_supabase(url, key)

    counts = import_into_sqlite(export, args.db_path)
    for collection, count in counts.items():
        print(f"{collection}: {count} rows -> {args.db_path}")


if __name__ == "__main__":
    main()
