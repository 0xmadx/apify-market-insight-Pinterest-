"""`.dockerignore` and `.actorignore` must both hide the product.

    .venv/Scripts/python.exe -m tests.test_ignore_parity

WHY THIS EXISTS. CLAUDE.md states the rule — "Keep the two in step: anything
added to .dockerignore belongs here too" — and until 2026-09-10 nothing
enforced it. Verified that day: 18 rules against 15, with no test, no script
and no CI step comparing them. The rule was prose sitting on the one path that
actually ships.

The lesson it encodes cost a real leak. `.dockerignore` governs what goes in
the IMAGE; the Apify CLI honours `.gitignore` and `.actorignore` for the
UPLOAD. On the first deploy all 176 tracked files went to Apify — `docs/wire/`
and `probes/results/` included, i.e. THE PRODUCT — while the image correctly
held 26. Two lists, one of them right.

WHY NOT A STRICT DIFF. The two files legitimately disagree: `.dockerignore`
excludes `.venv/`, `storage/`, `.git/` and `*.pyc` (build-context noise the
upload never sees anyway, since `.gitignore` already covers them), and
`.actorignore` carries un-ignore rules like `!.actor/README.md` that have no
meaning for Docker. Asserting the files are identical would fail on day one
for reasons that are not defects, and a test that cries wolf gets deleted.

So this asserts the INVARIANT instead: every path whose leak would hand over
the research — the thing being sold — is excluded by BOTH.
"""
import fnmatch
import pathlib
import sys

sys.path.insert(0, ".")

checks = []


def check(name, condition, detail=""):
    checks.append((name, bool(condition)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not condition else ""))


# The paths whose exposure IS the loss. Each is the reverse-engineering, the
# operator's own infrastructure, or tooling that describes both — never
# anything `python -m src.main` imports at runtime.
PRODUCT_PATHS = {
    "docs/":      "the measured map of an API with no contract — the product itself",
    "probes/":    "captured real responses, i.e. the same map in raw form",
    "tests/":     "encodes every trap and limit that was paid for in probes",
    "tools/":     "publish/pricing/deepseek — operator tooling, never run by the actor",
    "adspower/":  "describes the operator's own account + proxy infrastructure",
    "browsers/":  "the session farm, same reason",
    ".claude/":   "agent and skill definitions, including refusals and history",
    ".github/":   "CI config and secret names",
    "api_sim.py": "a local demo server; the actor's CMD never reaches it",
}


def rules(path):
    """Active exclusion rules — comments, blanks and un-ignore (!) lines out."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    out = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        out.add(line)
    return out


def ignored(path, ignorefile=".actorignore"):
    """Would `apify push` strip this path? gitignore-style: the LAST matching
    rule wins, and a leading `!` un-ignores.

    Deliberately a simulation rather than a call to the CLI — the question is
    what the rules say, and that must be answerable offline, in CI, without a
    token or a network.
    """
    text = pathlib.Path(ignorefile).read_text(encoding="utf-8")
    verdict = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pat = line[1:] if negate else line
        pat = pat.lstrip("/")
        hit = (fnmatch.fnmatch(path, pat)
               or fnmatch.fnmatch(pathlib.PurePosixPath(path).name, pat)
               or path.startswith(pat.rstrip("/") + "/"))
        if hit:
            verdict = not negate
    return verdict


def covers(ruleset, path):
    """Is `path` excluded by this ruleset? Tolerates the trailing-slash and
    leading-slash spellings the two formats use interchangeably."""
    bare = path.rstrip("/")
    return any(r.rstrip("/").lstrip("/") == bare.lstrip("/") for r in ruleset)


def main():
    docker = rules(".dockerignore")
    actor = rules(".actorignore")

    print(f"\n.dockerignore: {len(docker)} active rules · "
          f".actorignore: {len(actor)} active rules")

    print("\nevery product path is hidden from the IMAGE")
    for path, why in sorted(PRODUCT_PATHS.items()):
        check(f"dockerignore excludes {path}", covers(docker, path), why)

    print("\n...and from the UPLOAD, which is the one that leaked")
    for path, why in sorted(PRODUCT_PATHS.items()):
        check(f"actorignore excludes {path}", covers(actor, path), why)

    print("\nsecrets are hidden from the upload regardless of spelling")
    check("actorignore excludes .env", covers(actor, ".env"))
    check("actorignore also excludes the .env.* variants "
          "(a .env.backup-premigration was committed once)",
          any(r.startswith(".env") and "*" in r for r in actor),
          sorted(r for r in actor if r.startswith(".env")))

    # The drift guard proper: a NEW product-ish exclusion added to one file and
    # forgotten in the other is exactly how the 176-file leak happened. Only
    # directory rules are compared — file globs like *.pyc are build noise.
    print("\ndrift: a directory hidden from the image is hidden from the upload")
    docker_dirs = {r for r in docker if r.endswith("/")
                   and not r.startswith("**")
                   and r not in {".venv/", "storage/", "apify_storage/", ".git/"}}
    missing = sorted(d for d in docker_dirs if not covers(actor, d))
    check("no directory excluded from the image is missing from the upload list",
          not missing, f"missing from .actorignore: {missing}")

    # The inverse guard. Everything above asks "is this hidden?". This asks
    # "is this still THERE?" -- because on the Apify Store a missing README is
    # a blank listing page, and a blank listing fails nothing, warns nobody,
    # and is seen first by a paying customer.
    #
    # WHAT THIS IS AND IS NOT, stated carefully because the first version of
    # this comment got it wrong. `*.md` excludes every README in the repo and
    # `!.actor/README.md` rescued only the general actor's, so on paper the
    # three persona READMEs were excluded. Checked against the live platform on
    # 2026-09-10: all four listings serve their full README. The CLI uploads
    # the file a manifest's `readme` field names regardless of the ignore rules.
    #
    # This is therefore a guard against undocumented behaviour changing, not a
    # fix for a live outage. Worth keeping at 4 assertions; not worth claiming
    # as a bug that was caught in production.
    print("\nthe Store listing page survives the upload (all four actors)")
    for readme in ["\N{FULL STOP}actor/README.md".replace("\N{FULL STOP}", "."),
                   "actors/marketers/README.md",
                   "actors/ecommerce/README.md",
                   "actors/creators/README.md"]:
        check(f"{readme} is uploaded, not stripped",
              not ignored(readme),
              "matched *.md with no un-ignore rule — this actor would publish "
              "with an empty description")
        check(f"{readme} exists on disk", pathlib.Path(readme).is_file())

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
