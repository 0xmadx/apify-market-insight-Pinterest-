"""The `quickStart` dropdown — one-click recipes for the three persona actors.

    .venv/Scripts/python.exe -m tests.test_quick_start

Two things this suite exists to catch:

  1. **The name collision with Pinterest's own `preset` field.** JSON tolerates
     duplicate keys silently (last one wins), so a schema mistake here would
     not raise an error — it would just make the dropdown quietly do nothing,
     or worse, feed a string where Pinterest expects the integer 1-4. Caught
     once already during development; guarded here so it cannot come back.
  2. **Every recipe an input_schema.json's `quickStart` enum offers a customer
     actually exists** in `QUICK_START_RECIPES` — an enum value with no
     matching recipe would silently no-op for that customer, who would have no
     way to know their selection did nothing.
"""
import json
import sys

sys.path.insert(0, ".")

from src.main import _apply_quick_start, QUICK_START_RECIPES  # noqa: E402

checks = []


def check(name, condition, detail=""):
    checks.append((name, bool(condition)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not condition else ""))


SCHEMA_FILES = [
    "actors/marketers/input_schema.json",
    "actors/ecommerce/input_schema.json",
    "actors/creators/input_schema.json",
]


def main():
    # A1 — absent quickStart is a true no-op: nothing added, nothing removed.
    task = {"operation": "keywords", "region": "FR"}
    result = _apply_quick_start(dict(task))
    check("A1: no quickStart leaves the task byte-for-byte unchanged",
          result == task, result)

    # A2 — "custom" removes the key but changes nothing else.
    result = _apply_quick_start({"quickStart": "custom", "operation": "shopping"})
    check("A2: quickStart=custom drops the key, keeps every other field",
          result == {"operation": "shopping"}, result)

    # A3 — an unrecognized name never crashes and never silently mutates
    # fields — same as "custom".
    result = _apply_quick_start({"quickStart": "not-a-real-one", "operation": "radar"})
    check("A3: an unknown quickStart name is a safe no-op, not a crash",
          result == {"operation": "radar"}, result)

    # A4 — a real recipe's fields WIN over what the form submitted for the
    # same key (this is the entire point: picking a recipe must actually
    # change the operation, not silently lose to a schema default).
    result = _apply_quick_start(
        {"quickStart": "campaign_timing", "operation": "radar", "region": "GB+IE"})
    check("A4: a recipe's fields override the same key from the task",
          result == {"operation": "moments", "region": "GB+IE"}, result)

    # A5 — a multi-field recipe lands every field it defines, and leaves
    # fields it does NOT define (queries) completely untouched.
    result = _apply_quick_start(
        {"quickStart": "validate_my_angle", "queries": ["fall wedding decor"]})
    check("A5: a multi-field recipe sets all its fields, preserves the rest",
          result == {"operation": "keywords", "mode": "exact",
                     "includeRelated": True, "queries": ["fall wedding decor"]},
          result)

    # B1 — no name collision: Pinterest's own [keywords] `preset` (an
    # integer, 1-4) must survive untouched in every schema that also has a
    # `quickStart` field. This is the exact bug this suite exists to prevent.
    for path in SCHEMA_FILES:
        schema = json.load(open(path, encoding="utf-8"))
        props = schema["properties"]
        check(f"B1: {path} keeps quickStart and Pinterest's own preset distinct",
              "quickStart" in props and "preset" in props
              and props["preset"]["type"] == "integer",
              props.get("preset"))

    # B2 — every enum value a schema offers a customer resolves to a real
    # recipe (except the sentinel "custom", which is a deliberate no-op).
    for path in SCHEMA_FILES:
        schema = json.load(open(path, encoding="utf-8"))
        enum = schema["properties"]["quickStart"]["enum"]
        offered = [v for v in enum if v != "custom"]
        missing = [v for v in offered if v not in QUICK_START_RECIPES]
        check(f"B2: {path} offers no dropdown option without a matching recipe",
              not missing, missing)
        # enumTitles must exist and line up 1:1 with enum, or the dropdown
        # shows raw ids instead of the human-readable labels.
        titles = schema["properties"]["quickStart"].get("enumTitles")
        check(f"B2: {path} has one enumTitle per enum value",
              titles is not None and len(titles) == len(enum),
              f"{len(titles) if titles else 0} titles / {len(enum)} values")

    # B3 — every recipe's operation is one QUICK_START_RECIPES actually knows
    # how to run (catches a typo'd operation name in a recipe definition).
    valid_ops = {"shopping", "keywords", "moments", "radar", "crawl"}
    for name, recipe in QUICK_START_RECIPES.items():
        check(f"B3: recipe '{name}' targets a real operation",
              recipe.get("operation") in valid_ops, recipe.get("operation"))

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
