"""Study template: ``run(ctx)`` returns a JSON-serializable dict (ADR 0013).

ctx.market  point-in-time prices and FX (bound to as_of)
ctx.series  macro and index series (bound to as_of)
ctx.lake(ds, **where)  clean lake rows known at as_of
ctx.params, ctx.seed, ctx.rng, ctx.note(text)

Use qp functions for every calculation (one implementation per calculation).
"""

from __future__ import annotations

from typing import Any


def run(ctx: Any) -> dict[str, Any]:
    ctx.note("describe data choices here; they are saved with the result")
    return {"answer": None}
