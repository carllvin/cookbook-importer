#!/usr/bin/env python3
"""
Deletes ALL recipes in Tandoor except 10 - by default the 10 with the lowest
id (oldest), or pick exactly which ones with --keep-ids.

WARNING: the most destructive script in this project. There is no undo. Dry
run by default; --apply additionally requires typing DELETE to confirm.

Usage (run inside the container, from the backend/ directory):

    python scripts/purge_recipes.py                       # dry run: shows what would be kept/deleted
    python scripts/purge_recipes.py --keep-ids 4,17,102    # choose exactly which ones to keep
    python scripts/purge_recipes.py --keep-latest          # keep the 10 newest (highest id) instead of oldest
    python scripts/purge_recipes.py --apply                # actually delete (asks to type DELETE first)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import tandoor_client  # noqa: E402
from app.tandoor_client import TandoorError  # noqa: E402
from scripts._shared import print_header  # noqa: E402


def fetch_all_recipes_compact(client) -> list[dict]:
    recipes = []
    resp = client.get("/recipe/", params={"page_size": 200})
    resp.raise_for_status()
    data = resp.json()
    while True:
        results = data.get("results", data) if isinstance(data, dict) else data
        recipes.extend({"id": r["id"], "name": r.get("name", "")} for r in results)
        next_url = data.get("next") if isinstance(data, dict) else None
        if not next_url:
            break
        resp = client.get(next_url)
        resp.raise_for_status()
        data = resp.json()
    return recipes


def run(keep_count: int, keep_ids: list[int] | None, keep_latest: bool, apply: bool) -> None:
    with tandoor_client.get_client() as client:
        recipes = fetch_all_recipes_compact(client)
        print(f"{len(recipes)} recipe(s) in total.")

        if keep_ids:
            keep_set = set(keep_ids)
            missing = keep_set - {r["id"] for r in recipes}
            if missing:
                print(f"Warning: these --keep-ids don't exist: {sorted(missing)}")
            keep = [r for r in recipes if r["id"] in keep_set]
        else:
            ordered = sorted(recipes, key=lambda r: r["id"], reverse=keep_latest)
            keep = ordered[:keep_count]

        keep_id_set = {r["id"] for r in keep}
        to_delete = [r for r in recipes if r["id"] not in keep_id_set]

        print_header(f"Keeping {len(keep)} recipe(s):")
        for r in keep:
            print(f"  - {r['name']!r} (#{r['id']})")

        print_header(f"Deleting {len(to_delete)} recipe(s):")
        for r in to_delete[:20]:
            print(f"  - {r['name']!r} (#{r['id']})")
        if len(to_delete) > 20:
            print(f"  ... and {len(to_delete) - 20} more")

        if not to_delete:
            print("\nNothing to delete.")
            return

        if not apply:
            print("\nDry run - nothing was changed. Re-run with --apply to actually delete these.")
            return

        print(f"\nWARNING: this will PERMANENTLY delete {len(to_delete)} recipe(s). There is no undo.")
        answer = input("Type DELETE to confirm: ").strip()
        if answer != "DELETE":
            print("Not confirmed - aborting, nothing was deleted.")
            return

        deleted, errors = 0, 0
        for r in to_delete:
            try:
                tandoor_client.delete_recipe(client, r["id"])
                deleted += 1
            except TandoorError as exc:
                print(f"  ! could not delete {r['name']!r} (#{r['id']}): {exc}")
                errors += 1

        print_header("Summary")
        print(f"{deleted} recipe(s) deleted, {errors} error(s). {len(keep)} recipe(s) remain.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep-count", type=int, default=10, help="How many recipes to keep (default 10).")
    parser.add_argument("--keep-ids", type=str, help="Comma-separated recipe ids to keep exactly (overrides --keep-count).")
    parser.add_argument("--keep-latest", action="store_true", help="Keep the highest-id (newest) recipes instead of the lowest-id (oldest).")
    parser.add_argument("--apply", action="store_true", help="Actually delete (still asks to type DELETE).")
    args = parser.parse_args()

    keep_ids = [int(x) for x in args.keep_ids.split(",")] if args.keep_ids else None

    try:
        run(args.keep_count, keep_ids, args.keep_latest, args.apply)
    except TandoorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
