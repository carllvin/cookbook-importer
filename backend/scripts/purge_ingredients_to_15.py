#!/usr/bin/env python3
"""
Quick and dirty: deletes ALL foods except the first 15 (lowest id). For test
runs only - no undo, no usage checks, no cascade warnings like
purge_ingredients.py has. Deleting a food still used by a recipe removes
that ingredient line from the recipe too.

Usage:
    python scripts/purge_ingredients_to_15.py            # dry run, lists what would be deleted
    python scripts/purge_ingredients_to_15.py --apply      # actually deletes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import tandoor_client  # noqa: E402

KEEP_COUNT = 15


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    with tandoor_client.get_client() as client:
        foods = sorted(tandoor_client.fetch_all_items(client, "food"), key=lambda f: f["id"])
        keep, delete = foods[:KEEP_COUNT], foods[KEEP_COUNT:]

        print(f"{len(foods)} food(s) total. Keeping {len(keep)}, deleting {len(delete)}.")
        for f in delete:
            print(f"  - {f['name']!r} (#{f['id']})")

        if not args.apply:
            print("\nDry run - re-run with --apply to actually delete.")
            return

        if input(f"\nDelete {len(delete)} food(s)? [y/N] ").strip().lower() != "y":
            print("Aborted.")
            return

        for f in delete:
            resp = client.delete(f"/food/{f['id']}/")
            print(f"  {f['name']!r} -> {resp.status_code}")


if __name__ == "__main__":
    main()
