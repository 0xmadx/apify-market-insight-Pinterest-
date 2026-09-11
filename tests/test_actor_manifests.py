"""All four actor manifests must be pushable and internally consistent.

    .venv/Scripts/python.exe -m tests.test_actor_manifests

WHY THIS EXISTS. Four `actor.json` files describe four Store listings that all
run the SAME `src/`. Nothing checked them against each other or against the
platform's limits, so every constraint below was enforced only by a push
failing — or worse, by not failing.

THE ONE THAT ALREADY COST A DEPLOY. Apify rejects an Actor whose **title**
exceeds 63 characters, and reports it as "Actor name must be at most 63
characters long". The message names the wrong field: `name` was well under the
limit and `title` was over it. That mis-reading burned a real debugging session
on 2026-09-02 before the limit was measured with `wc -m`. A push is the slowest
possible place to learn this; it is a string length, checkable offline.

THE ONES THAT WOULD NOT FAIL AT ALL:
  * a lost `defaultMemoryMbytes` pin bills 16x more per run (it was 4096 until
    2026-08-27, against a measured 62 MB peak) and nothing errors
  * a duplicated `name` silently pushes one actor over another
  * a `readme`/`input`/`dockerfile` path that does not resolve ships a listing
    with no description, or a build with no Dockerfile
  * a `buildTag` other than `latest` deploys where no customer is looking
"""
import json
import pathlib
import sys

sys.path.insert(0, ".")

checks = []


def check(name, condition, detail=""):
    checks.append((name, bool(condition)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not condition else ""))


# Apify's own cap. Measured, not guessed — and it applies to `title`, which is
# NOT what the error message says.
TITLE_MAX = 63

MANIFESTS = [
    ".actor/actor.json",
    "actors/marketers/actor.json",
    "actors/ecommerce/actor.json",
    "actors/creators/actor.json",
]


def main():
    loaded = {}

    print("\nevery manifest parses and is where it is expected")
    for path in MANIFESTS:
        p = pathlib.Path(path)
        ok = p.is_file()
        check(f"{path} exists", ok)
        if not ok:
            continue
        try:
            loaded[path] = json.loads(p.read_text(encoding="utf-8"))
            check(f"{path} is valid JSON", True)
        except json.JSONDecodeError as exc:
            check(f"{path} is valid JSON", False, str(exc))

    print(f"\ntitle fits Apify's {TITLE_MAX}-character cap")
    print("  (the platform reports this as 'Actor name must be at most 63'")
    print("   characters long' — it means TITLE, and that cost a session)")
    for path, m in loaded.items():
        title = m.get("title", "")
        n = len(title)
        check(f"{path} title is {n} chars",
              n <= TITLE_MAX,
              f"{n} chars, {n - TITLE_MAX} over: {title!r}")

    print("\nnames are present and unique — a duplicate pushes one over another")
    names = {}
    for path, m in loaded.items():
        name = m.get("name")
        check(f"{path} declares a name", bool(name))
        if name:
            names.setdefault(name, []).append(path)
    for name, paths in sorted(names.items()):
        check(f"name {name!r} is used by exactly one manifest",
              len(paths) == 1, f"also used by: {paths}")

    print("\nthe cost pin survived (256 MB against a measured 62 MB peak)")
    for path, m in loaded.items():
        mem = m.get("defaultMemoryMbytes")
        check(f"{path} pins defaultMemoryMbytes to 256",
              mem == 256,
              f"is {mem} — 4096 was the old value and bills 16x")

    print("\nbuildTag is 'latest' on all four, so a push reaches customers")
    for path, m in loaded.items():
        tag = m.get("buildTag")
        check(f"{path} buildTag is 'latest'", tag == "latest",
              f"is {tag!r} — a push would deploy where nobody is looking")

    # Every path inside a manifest is relative to that manifest's own directory.
    # This is where the persona actors differ from the root one (`../Dockerfile`
    # vs `../../Dockerfile`), and getting it wrong breaks the build or the
    # listing, not the tests.
    print("\nevery path a manifest names actually resolves")
    for path, m in loaded.items():
        base = pathlib.Path(path).parent
        for field in ("dockerfile", "input", "output", "readme"):
            rel = m.get(field)
            if rel is None:
                check(f"{path} declares {field}", False,
                      "missing — the platform falls back to a default that may "
                      "not be what this actor wants")
                continue
            target = (base / rel).resolve()
            check(f"{path} {field} -> {rel} resolves",
                  target.is_file(), f"no such file: {target}")

    print("\nall four share one Dockerfile — they run the same src/")
    dockerfiles = set()
    for path, m in loaded.items():
        rel = m.get("dockerfile")
        if rel:
            dockerfiles.add((pathlib.Path(path).parent / rel).resolve())
    check("every manifest points at the same Dockerfile",
          len(dockerfiles) == 1,
          f"{len(dockerfiles)} distinct: {sorted(str(d) for d in dockerfiles)}")

    print("\nthe input schema each manifest names is the one beside it")
    for path, m in loaded.items():
        base = pathlib.Path(path).parent
        rel = m.get("input")
        if not rel:
            continue
        target = base / rel
        if not target.is_file():
            continue
        try:
            schema = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            check(f"{target} is valid JSON", False, str(exc))
            continue
        props = schema.get("properties", {})
        check(f"{target} declares an operation field", "operation" in props)
        op = props.get("operation", {})
        default = op.get("default")
        enum = op.get("enum", [])
        check(f"{target} operation default {default!r} is in its own enum",
              default in enum, f"default={default!r} enum={enum}")

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
