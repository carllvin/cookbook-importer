#!/usr/bin/env python3
"""
Deletes ALL foods (ingredients) in Tandoor except 80 - by default the 80
most-used ones (kept via each food's usage count across your recipes), or
pick exactly which ones with --keep-ids.

WARNING: deleting a food that a KEPT recipe still uses does not just remove
the food entry - Tandoor cascades the deletion to that recipe's ingredient
line too, silently stripping the ingredient out of a recipe you never asked
to touch. For that reason, by default this script ONLY deletes foods with
ZERO usage among the ones not being kept; foods that are still used by at
least one recipe are listed but skipped unless you pass --allow-cascade (and
then confirm a second time, since that's the genuinely destructive part).

Dry run by default. --apply additionally requires typing DELETE to confirm
(and CASCADE as a second confirmation if --allow-cascade applies to anything).

Usage (run inside the container, from the backend/ directory):

    python scripts/purge_ingredients.py                       # dry run: shows what would be kept/deleted/skipped
    python scripts/purge_ingredients.py --keep-ids 4,17,102    # choose exactly which ones to keep
    python scripts/purge_ingredients.py --apply                 # delete the zero-usage ones (asks to type DELETE)
    python scripts/purge_ingredients.py --apply --allow-cascade  # also delete still-used ones (asks twice)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import tandoor_client  # noqa: E402
from app.tandoor_client import TandoorError  # noqa: E402
from scripts._shared import compute_usage_maps, fetch_all_recipes_full, print_header  # noqa: E402


def run(keep_count: int, keep_ids: list[int] | None, allow_cascade: bool, apply: bool) -> None:
    with tandoor_client.get_client() as client:
        foods = tandoor_client.fetch_all_items(client, "food")
        print(f"{len(foods)} food(s) in total.")

        print("Scanning every recipe's full detail once (to count ingredient usage)...")
        recipes = fetch_all_recipes_full(client)
        usage = compute_usage_maps(recipes)["food"]
        print(f"  {len(recipes)} recipe(s) scanned.")

        if keep_ids:
            keep_set = set(keep_ids)
            missing = keep_set - {f["id"] for f in foods}
            if missing:
                print(f"Warning: these --keep-ids don't exist: {sorted(missing)}")
            keep = [f for f in foods if f["id"] in keep_set]
        else:
            ordered = sorted(foods, key=lambda f: (-usage.get(f["id"], 0), f["id"]))
            keep = ordered[:keep_count]

        keep_id_set = {f["id"] for f in keep}
        candidates = [f for f in foods if f["id"] not in keep_id_set]
        safe_to_delete = [f for f in candidates if usage.get(f["id"], 0) == 0]
        in_use = [f for f in candidates if usage.get(f["id"], 0) > 0]

        print_header(f"Keeping {len(keep)} food(s) (most-used first):")
        for f in keep[:20]:
            print(f"  - {f['name']!r} (#{f['id']}) - used by {usage.get(f['id'], 0)} recipe(s)")
        if len(keep) > 20:
            print(f"  ... and {len(keep) - 20} more")

        print_header(f"Deleting {len(safe_to_delete)} unused food(s):")
        for f in safe_to_delete[:20]:
            print(f"  - {f['name']!r} (#{f['id']})")
        if len(safe_to_delete) > 20:
            print(f"  ... and {len(safe_to_delete) - 20} more")

        if in_use:
            print_header(f"Skipping {len(in_use)} still-used food(s) (would strip them from recipes you're keeping):")
            for f in in_use[:20]:
                print(f"  - {f['name']!r} (#{f['id']}) - used by {usage[f['id']]} recipe(s)")
            if len(in_use) > 20:
                print(f"  ... and {len(in_use) - 20} more")
            print("  (pass --allow-cascade to delete these too, at the cost of removing them from those recipes)")

        if not safe_to_delete and not (in_use and allow_cascade):
            print("\nNothing to delete.")
            return

        if not apply:
            print("\nDry run - nothing was changed. Re-run with --apply to actually delete.")
            return

        print(f"\nWARNING: this will PERMANENTLY delete {len(safe_to_delete)} unused food(s). There is no undo.")
        answer = input("Type DELETE to confirm: ").strip()
        if answer != "DELETE":
            print("Not confirmed - aborting, nothing was deleted.")
            return

        deleted, errors = 0, 0
        for f in safe_to_delete:
            try:
                resp = client.delete(f"/food/{f['id']}/")
                if resp.status_code in (200, 202, 204):
                    deleted += 1
                else:
                    print(f"  ! could not delete {f['name']!r} (#{f['id']}): {resp.status_code} {resp.text[:200]}")
                    errors += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  ! could not delete {f['name']!r} (#{f['id']}): {exc}")
                errors += 1

        cascade_deleted = 0
        if in_use and allow_cascade:
            print(f"\nWARNING: --allow-cascade will PERMANENTLY delete {len(in_use)} more food(s) still used by "
                  f"recipes - those recipes will lose that ingredient line. There is no undo.")
            answer2 = input("Type CASCADE to confirm: ").strip()
            if answer2 == "CASCADE":
                for f in in_use:
                    try:
                        resp = client.delete(f"/food/{f['id']}/")
                        if resp.status_code in (200, 202, 204):
                            cascade_deleted += 1
                        else:
                            print(f"  ! could not delete {f['name']!r} (#{f['id']}): {resp.status_code} {resp.text[:200]}")
                            errors += 1
                    except Exception as exc:  # noqa: BLE001
                        print(f"  ! could not delete {f['name']!r} (#{f['id']}): {exc}")
                        errors += 1
            else:
                print("Not confirmed - the still-used foods were left alone.")

        print_header("Summary")
        print(f"{deleted} unused food(s) deleted, {cascade_deleted} still-used food(s) cascade-deleted, "
              f"{errors} error(s). {len(keep)} food(s) remain (plus {len(in_use) - cascade_deleted} skipped in-use ones, if any).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep-count", type=int, default=80, help="How many foods to keep (default 80).")
    parser.add_argument("--keep-ids", type=str, help="Comma-separated food ids to keep exactly (overrides --keep-count).")
    parser.add_argument("--allow-cascade", action="store_true",
                         help="Also delete foods still used by a kept recipe (removes that ingredient line there). Asks for a second confirmation.")
    parser.add_argument("--apply", action="store_true", help="Actually delete (still asks to type DELETE / CASCADE).")
    args = parser.parse_args()

    keep_ids = [int(x) for x in args.keep_ids.split(",")] if args.keep_ids else None

    try:
        run(args.keep_count, keep_ids, args.allow_cascade, args.apply)
    except TandoorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
