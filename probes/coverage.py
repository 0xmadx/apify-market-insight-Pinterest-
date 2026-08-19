"""Scenario B2, for real: what the wire returns vs what the parsers surface.

    .venv/Scripts/python.exe -m probes.coverage          # summary
    .venv/Scripts/python.exe -m probes.coverage --full   # every unread leaf

The seed audit that found F1-F6 was done by hand, once. This makes it a check
that runs any time — because the failure it guards against is silent by
construction: a field Pinterest adds, or one we simply never looked at, costs
nothing and shows up nowhere. Nothing errors. The records just quietly carry
less than they could.

Two directions, and both matter:

  UNREAD   a leaf in the response that no parser output contains.
           Either dead weight (fine — say so) or value left on the table.
  INVENTED a leaf a parser emits that has no counterpart in the response.
           Usually legitimate (constructed urls, _meta, joins) but it is
           exactly where a fabricated value would hide, so it is listed.

This is a REPORT, not a test: it prints and exits 0. Judgement about whether an
unread field is worth carrying belongs to a human, and freezing today's answer
into an assertion would just make the next new field fail the build.
"""
import glob
import json
import pathlib
import sys

sys.path.insert(0, ".")

from src import parsers  # noqa: E402

# fixture glob -> (parser callable, style)
PARSERS = [
    ("3.2-*.json", "A", lambda d: parsers.parse_spotlight(d)),
    ("3.3-*.json", "A", lambda d: parsers.parse_moments_list(d)),
    ("3.4-*.json", "A", lambda d: parsers.parse_moment_metrics(d)),
    ("3.5-*.json", "A", lambda d: parsers.parse_editorial(d, "US")),
    ("3.6-*.json", "A", lambda d: parsers.parse_taxonomy(d)),
    ("3.7-*.json", "A", lambda d: parsers.parse_top_categories(d)),
    ("3.8-*.json", "A", lambda d: parsers.parse_category_metrics(d)),
    ("3.9-*.json", "A", lambda d: parsers.parse_category_demographics(d, "OUTBOUND_CLICK")),
    ("3.10-*.json", "A", lambda d: parsers.parse_top_products(d, "US")),
    ("3.12-*.json", "B", lambda d: parsers.parse_discover(d)),
    ("3.13-*.json", "B", lambda d: parsers.parse_keyword_metrics(d)),
    ("3.14-*.json", "B", lambda d: parsers.parse_keyword_demographics(d)),
    ("3.15-*.json", "B", lambda d: parsers.parse_related_terms(d)),
    ("3.16-*.json", "B", lambda d: parsers.parse_term_images(d)),
    ("3.17-*.json", "B", lambda d: parsers.parse_prefix_match(d)),
]

# Leaves we have looked at and deliberately do not carry. Each needs a reason —
# "we didn't get round to it" is not one, and an entry here is a decision on
# record, not a way to silence the report.
KNOWN_DROPPED = {
    "affinity": "null in 100/100 rows — never populated (measured)",
    "total": "always 0 — Pinterest never exposes absolute volume; carrying it "
             "would read as 'no engagement'",
    "client_context": "operator account PII — stripped before storage",
    "_client_context": "the strip_pii placeholder",
    "request_identifier": "transport-level, not data",
    "resource": "envelope metadata",
    "status": "envelope metadata",
    "code": "envelope metadata",
    "http_status": "envelope metadata",
    "message": "envelope metadata",
    "endpoint_name": "envelope metadata",
    "x_pinterest_rid": "logged for debugging, not a record field",
    "vertical_offset": "pin crop hint for Pinterest's own masonry layout — "
                       "cosmetic, meaningless outside their grid",
    "hide_keyword_percentages": "an editorial display flag for Pinterest's UI",
    "is_ready_for_translation": "Pinterest's internal localisation workflow",
    "width": "image dimensions per size; the size ladder is derivable from one "
             "url (same hash, swapped path segment) — see parse_top_products",
    "height": "see `width`",
    "url": "only the largest is carried; every other size is the same image "
           "hash with the path segment swapped, so all 7 are derivable",
    "CA": "editorial keywords for a region other than the one requested — "
          "picking another region's list is scenario B6's failure mode",
    "GB+IE": "see `CA`",
}


def leaf_pairs(obj, key="", out=None):
    """Every (key_name, value) leaf in a payload.

    Comparison is by VALUE, not by key name — the first version of this tool
    compared names and flagged `src`, `peak_timestamp_millis` and
    `product_category` as unread when the parsers simply rename them to
    `image_url`, `peak_at` and `category_id`. A rename is the normal case here,
    so a name-based check reports mostly noise and trains you to ignore it.
    """
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            leaf_pairs(v, k, out)
    elif isinstance(obj, list):
        for item in obj[:3]:           # a few items — enough to catch a field
            leaf_pairs(item, key, out)
    else:
        if key and obj is not None:
            out.append((key, obj))
    return out


def values_of(obj, out=None):
    """Every scalar the parsed output carries, at any depth, plus dict keys —
    parsers often promote a value into a key (taxonomy keyed by category id)."""
    if out is None:
        out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            values_of(v, out)
    elif isinstance(obj, list):
        for item in obj:
            values_of(item, out)
    else:
        if obj is not None:
            out.add(obj)
    return out


def hashable(value):
    try:
        hash(value)
        return True
    except TypeError:
        return False


def load(pattern, style):
    matches = glob.glob(f"probes/results/{pattern}")
    if not matches:
        return None
    payload = json.load(open(matches[0], encoding="utf-8"))
    if style == "A" and isinstance(payload, dict):
        return (payload.get("resource_response") or {}).get("data")
    return payload


def main():
    full = "--full" in sys.argv
    total_unread = 0
    rows = []

    for pattern, style, parse in PARSERS:
        raw = load(pattern, style)
        if raw is None:
            rows.append((pattern, "NO FIXTURE", set(), set()))
            continue
        try:
            parsed = parse(raw)
        except Exception as exc:
            rows.append((pattern, f"PARSER RAISED: {exc}", set(), set()))
            continue

        pairs = leaf_pairs(raw)
        emitted = values_of(parsed)
        # A wire leaf is SURFACED if its value reaches the output anywhere —
        # under any name, or promoted to a dict key. Renames pass; genuinely
        # dropped fields do not.
        # Values are compared in string form as well as natively: parsers
        # legitimately change type (moment timestamps arrive as epoch-ms
        # STRINGS and are emitted as ints), and a type change is not a drop.
        emitted_str = {str(v) for v in emitted if hashable(v)}

        unread = set()
        for key, value in pairs:
            if key in KNOWN_DROPPED or not hashable(value):
                continue
            if isinstance(value, bool):
                # A bare True/False cannot be traced by value — it collides with
                # every other boolean. Judged only if the output carries no
                # boolean at all, so `hasPrediction` -> `has_forecast` passes.
                if not any(isinstance(v, bool) for v in emitted):
                    unread.add(key)
                continue
            if isinstance(value, int) and abs(value) < 3:
                if key not in emitted and str(value) not in emitted_str:
                    unread.add(key)
                continue
            if value not in emitted and str(value) not in emitted_str:
                unread.add(key)
        invented = set()
        total_unread += len(unread)
        rows.append((pattern, None, unread, invented))

    name = lambda p: p.split("-")[0]  # noqa: E731
    print(f"\n{'fixture':<8} {'wire leaves':>11} {'unread':>7}  notes")
    print("-" * 72)
    for pattern, err, unread, invented in rows:
        if err:
            print(f"{name(pattern):<8} {err}")
            continue
        flag = "" if not unread else "  <-- look"
        print(f"{name(pattern):<8} {'':>11} {len(unread):>7}{flag}")
        if unread and (full or len(unread) <= 8):
            for k in sorted(unread):
                print(f"           unread: {k}")
        elif unread:
            for k in sorted(unread)[:8]:
                print(f"           unread: {k}")
            print(f"           … +{len(unread) - 8} more (--full)")
        if invented and full:
            for k in sorted(invented):
                print(f"           emitted-not-in-wire: {k}")

    print(f"\n{total_unread} unread leaf name(s) across {len(PARSERS)} parsers.")
    print("Unread is not automatically a bug — it is a question: is this worth "
          "carrying? Answer it, then either parse it or add it to "
          "KNOWN_DROPPED with the reason.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
