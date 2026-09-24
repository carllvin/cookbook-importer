"""Re-exports app.tandoor_helpers, so every scripts/*.py file that does
`from scripts._shared import X` keeps working unchanged. The actual logic
now lives in app/tandoor_helpers.py, so the web app's tools (backend/app/)
and these CLI scripts share exactly one implementation - not two copies that
can silently drift apart (that's literally the bug class that motivated this
move: cleanup_units.py and cleanup_tandoor_duplicates.py each had their own
copy of the same verification logic, and one of them had a bug the other
didn't)."""
from __future__ import annotations

from app.tandoor_helpers import (  # noqa: F401
    TokenTracker,
    chunked,
    compute_usage_maps,
    entity_still_referenced,
    estimate_cost,
    fetch_all_recipes_full,
    find_recipes_by_filter,
    find_recipes_using_unit,
    format_cost_estimate,
    minimal_ref,
    print_header,
    resolve_name_collisions,
    validate_actions,
)
