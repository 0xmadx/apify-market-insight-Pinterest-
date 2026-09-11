"""The three places that run the suites must run the SAME suites.

    .venv/Scripts/python.exe -m tests.test_ci_contract

WHY THIS EXISTS. The suite list is written out by hand in three files —
`ship.sh` (the local gate), `.github/workflows/gate.yml` (CI on every push) and
`.github/workflows/deploy.yml` (the break-glass release). Nothing connected
them. Three hand-maintained copies of one list agreeing is luck, and luck
expires: the failure mode is adding a suite to `ship.sh`, watching it pass
locally forever, and never learning that CI has not run it once.

That is this project's signature defect — a plausible-looking success — aimed at
the machinery that is supposed to catch the others. A gate that silently checks
less than you think is worse than no gate, because it is trusted.

This test deliberately PARSES the three files rather than holding a fourth copy
of the list. A hardcoded expectation here would be the same bug wearing a
different hat, and it would go stale the first time a suite is legitimately
added.
"""
import pathlib
import re
import sys

sys.path.insert(0, ".")

checks = []


def check(name, condition, detail=""):
    checks.append((name, bool(condition)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not condition else ""))


# Each runner spells the loop differently — `for suite in ...` in bash, `for t
# in ...` in the workflows — but every entry is a bare `test_*` token inside a
# backslash-continued list. Pulling the tokens is both simpler and more robust
# than trying to parse shell or YAML properly.
RUNNERS = {
    "ship.sh":                       r"for suite in\s+((?:[^\n]|\\\n)+?);\s*do",
    ".github/workflows/gate.yml":    r"for t in\s+((?:[^\n]|\\\n)+?);\s*do",
    ".github/workflows/deploy.yml":  r"for t in\s+((?:[^\n]|\\\n)+?);\s*do",
}


def suites_in(path, pattern):
    text = pathlib.Path(path).read_text(encoding="utf-8")
    m = re.search(pattern, text)
    if not m:
        return None
    return set(re.findall(r"\btest_[a-z0-9_]+\b", m.group(1)))


def main():
    found = {}
    print("\nthe suite list each runner actually executes")
    for path, pattern in RUNNERS.items():
        suites = suites_in(path, pattern)
        check(f"{path} declares a parseable suite loop", suites is not None,
              "the loop's shape changed — this test can no longer read it, "
              "which means it is no longer guarding anything")
        if suites:
            found[path] = suites
            print(f"        {len(suites)} suites")

    if len(found) != len(RUNNERS):
        print("\ncannot compare lists — at least one runner did not parse")
        failed = [n for n, ok in checks if not ok]
        print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
        for name in failed:
            print(f"  FAILED: {name}")
        return 1

    print("\nthe three lists agree")
    reference = found["ship.sh"]
    for path, suites in found.items():
        if path == "ship.sh":
            continue
        missing = sorted(reference - suites)
        extra = sorted(suites - reference)
        check(f"{path} runs everything ship.sh runs", not missing,
              f"ship.sh runs these and {path} does not: {missing}")
        check(f"{path} runs nothing ship.sh skips", not extra,
              f"{path} runs these and ship.sh does not: {extra}")

    # The other half of the contract: a suite can exist on disk and be in none
    # of the lists, which looks exactly like a passing project. Every
    # tests/test_*.py is a runnable suite in this repo by convention — there are
    # no helper modules under that name — so absence from the lists is a defect,
    # not a choice.
    print("\nevery suite on disk is wired into the runners")
    on_disk = {p.stem for p in pathlib.Path("tests").glob("test_*.py")}
    print(f"        {len(on_disk)} suite files in tests/")
    unwired = sorted(on_disk - reference)
    check("no suite file exists that no runner executes", not unwired,
          f"written but never run: {unwired}")

    phantom = sorted(reference - on_disk)
    check("no runner names a suite that does not exist", not phantom,
          f"named in ship.sh but absent from tests/: {phantom}")

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
