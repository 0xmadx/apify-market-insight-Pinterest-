"""Generate the PRODUCT repo from this lab repo. One way, never the reverse.

WHY THIS EXISTS
---------------
Apify builds an Actor by CLONING the repository it is pointed at. Point it at
this repo and `docs/wire/` and `probes/results/` -- the reverse-engineered map
of an API with no contract, which is the actual product -- land on Apify's build
servers. `.dockerignore` keeps them out of the IMAGE; it does not keep them out
of the CLONE. That distinction already cost one deploy: 176 files went up while
the image correctly held 26.

So the repo Apify watches is a DIFFERENT repo, containing only what runs.

THE ONE RULE
------------
The product repo is GENERATED and NEVER HAND-EDITED. Everything flows lab ->
product. The moment someone edits the product repo directly the two drift and
nothing detects it -- this project's most-repeated failure (the 6379/6380 vault
split; the duplicate GitHub repo that drifted one commit apart within an hour).
A generated repo cannot drift. A forked one always does.

LAYOUT IT PRODUCES
------------------
Apify's monorepo shape, so several products share one engine:

    src/  Dockerfile  requirements.txt        the engine, one copy
    actors/general/    actor.json + input_schema.json + README.md
    actors/marketers/  ...                    (added later)

Each Apify Actor points at `<repo>#main:actors/<name>` with `dockerContextDir`
at the repo root, so every variant builds from the same `src/`.
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys

LAB = pathlib.Path(__file__).resolve().parent.parent

# The engine, shared by every product. Deliberately EXPLICIT rather than derived
# from .actorignore: a publish that silently includes a new file because an
# ignore pattern did not match is the bug this script exists to prevent. Adding
# a file to the product means adding it here, on purpose.
ENGINE = ["src", "Dockerfile", "requirements.txt", ".env.example"]

# Per-product: which files, and where they come from in the lab.
#
# Three variants added 2026-09-01, none of them a fork: each has its own
# actor.json (name/title/description) and its own input_schema.json (copied
# from .actor/'s, verified identical except title and operation.default), but
# every one of them still builds from the ONE shared src/ in ENGINE above.
# Same engine, three audiences -- marketers (campaign timing, search demand),
# ecommerce (trending products, real prices, merchant links), creators (a
# content calendar from moments + keywords). Not yet pushed anywhere or
# created on Apify; that stays a separate, explicit, operator-only action.
PRODUCTS = {
    "general": {"source": ".actor",
                "files": ["actor.json", "input_schema.json", "README.md"]},
    "marketers": {"source": "actors/marketers",
                 "files": ["actor.json", "input_schema.json", "README.md"]},
    "ecommerce": {"source": "actors/ecommerce",
                 "files": ["actor.json", "input_schema.json", "README.md"]},
    "creators": {"source": "actors/creators",
                "files": ["actor.json", "input_schema.json", "README.md"]},
}


def run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, text=True,
                          capture_output=True)


def lab_is_clean():
    """Refuse to publish from a dirty tree.

    The product repo records which lab commit produced it. From a dirty tree
    that reference is a lie: it names a commit that does not contain what was
    actually shipped.
    """
    return not run(["git", "status", "--porcelain"], cwd=LAB).stdout.strip()


def lab_commit():
    return run(["git", "rev-parse", "HEAD"], cwd=LAB).stdout.strip()


def build(dest, products):
    """Write the product tree into `dest`, replacing whatever is there."""
    for item in list(dest.iterdir()):
        if item.name == ".git":               # keep the clone's history
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()

    written = []
    for name in ENGINE:
        src = LAB / name
        if not src.exists():
            raise SystemExit("engine file missing from the lab: " + name)
        if src.is_dir():
            shutil.copytree(src, dest / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            written += [name + "/" + p.name for p in sorted(src.glob("*.py"))]
        else:
            shutil.copy2(src, dest / name)
            written.append(name)

    for product, spec in products.items():
        out = dest / "actors" / product
        out.mkdir(parents=True, exist_ok=True)
        for fname in spec["files"]:
            src = LAB / spec["source"] / fname
            if not src.exists():
                raise SystemExit(
                    product + ": " + spec["source"] + "/" + fname + " is "
                    "missing. The Apify Store description comes from README.md,"
                    " so a product without one publishes a blank listing.")
            shutil.copy2(src, out / fname)
            written.append("actors/" + product + "/" + fname)

        # dockerContextDir points at the repo root so every product builds from
        # the one shared src/. Set here, not in the lab's actor.json, because it
        # is a fact about the PRODUCT layout rather than about the lab.
        cfg = json.loads((out / "actor.json").read_text(encoding="utf-8"))
        cfg["dockerContextDir"] = "../.."
        cfg["dockerfile"] = "../../Dockerfile"
        cfg["input"] = "./input_schema.json"
        cfg["readme"] = "./README.md"
        (out / "actor.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n")

    # The product repo needs its OWN .dockerignore. The lab's excludes docs/
    # and probes/, which do not exist here -- and without one, `COPY . ./`
    # would put every product's actor.json and README into every product's
    # image. Apify reads those from the repo at build time; the image only ever
    # needs src/ and requirements.txt.
    (dest / ".dockerignore").write_text(
        "# Generated by tools/publish.py. Do not edit.\n"
        "#\n"
        "# Apify reads actors/<name>/actor.json and README.md from the REPO at\n"
        "# build time, so they do not belong in the image. Excluding them also\n"
        "# stops one product's image carrying every other product's config.\n"
        "actors/\n"
        ".git/\n"
        "**/__pycache__\n"
        "*.pyc\n"
        ".env\n"
        ".env.*\n"
        "!.env.example\n",
        encoding="utf-8", newline="\n")
    written.append(".dockerignore")

    (dest / "README.md").write_text(
        "# Pinterest Trends actors\n\n"
        "**Generated. Do not edit.**\n\n"
        "Every file here is produced by `tools/publish.py` in the private lab\n"
        "repository and force-pushed. Edits made here are overwritten without\n"
        "warning, and without a merge conflict to warn you.\n\n"
        "Built from lab commit `" + lab_commit() + "`.\n",
        encoding="utf-8", newline="\n")
    return written


def stage_actor(product, dest):
    """Assemble ONE product into a `.actor/`-rooted tree, for a DIRECT
    `apify push --dir <dest>`.

    WHY THIS EXISTS. `apify push`'s own help text is explicit: it deploys
    "using settings from '.actor/actor.json'" -- a literal path, not something
    `--dir` lets you rename. The `actors/<name>/actor.json` monorepo layout
    `build()` produces above only works through Apify's Git-repo-source
    console configuration (pointing an Actor at `<repo>#branch:actors/<name>`),
    which needs a product repo to exist. None does yet -- `--repo` has never
    actually been used. Until it is, direct `apify push` is the only PROVEN
    mechanism (it is how the first Actor, general, was created), and that
    mechanism needs a `.actor/` folder, so this builds one.
    """
    spec = PRODUCTS[product]
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / ".actor").mkdir()

    written = []
    for name in ENGINE:
        src = LAB / name
        if src.is_dir():
            shutil.copytree(src, dest / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dest / name)
        written.append(name)

    for fname in spec["files"]:
        src = LAB / spec["source"] / fname
        if not src.exists():
            raise SystemExit(product + ": " + spec["source"] + "/" + fname
                             + " is missing")
        shutil.copy2(src, dest / ".actor" / fname)
        written.append(".actor/" + fname)

    # Same reasoning as build()'s .dockerignore: this tree lives outside the
    # lab, so it needs its own, and without one `COPY . ./` would put every
    # OTHER product's leftover ENGINE copy (none here, but future-proof) or
    # dev cruft into the image.
    (dest / ".dockerignore").write_text(
        "**/__pycache__\n*.pyc\n.env\n.env.*\n!.env.example\n",
        encoding="utf-8", newline="\n")
    written.append(".dockerignore")
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", help="git URL of the PRODUCT repo")
    ap.add_argument("--dest", help="build into this directory instead of cloning")
    ap.add_argument("--stage-actor",
                    help="assemble ONE product into a .actor/-rooted tree at "
                         "--dest, for `apify push --dir <dest>` -- the working "
                         "mechanism until a product repo exists for --repo")
    ap.add_argument("--dry-run", action="store_true",
                    help="build and list, push nothing")
    ap.add_argument("--product", action="append",
                    help="publish only these (default: all)")
    args = ap.parse_args()

    if args.stage_actor:
        if not args.dest:
            raise SystemExit("--stage-actor needs --dest")
        if args.stage_actor not in PRODUCTS:
            raise SystemExit("no such product. known: " + ", ".join(PRODUCTS))
        if not lab_is_clean():
            raise SystemExit(
                "lab tree is dirty. Commit first -- same reason as any other "
                "publish: what gets pushed should match a real commit.")
        dest = pathlib.Path(args.dest).resolve()
        written = stage_actor(args.stage_actor, dest)
        print("  " + str(len(written)) + " files -> " + str(dest))
        for f in written:
            print("    " + f)
        print("\n  next: apify push --dir \"" + str(dest) + "\"")
        return 0

    if not args.repo and not args.dest:
        raise SystemExit("need --repo or --dest")
    if not lab_is_clean():
        raise SystemExit(
            "lab tree is dirty. The product repo records which lab commit "
            "produced it, so publishing now would name a commit that does not "
            "contain what shipped. Commit first.")

    products = ({k: v for k, v in PRODUCTS.items() if k in args.product}
                if args.product else PRODUCTS)
    if not products:
        raise SystemExit("no such product. known: " + ", ".join(PRODUCTS))

    if args.dest:
        dest = pathlib.Path(args.dest).resolve()
        dest.mkdir(parents=True, exist_ok=True)
        cloned = False
    else:
        dest = LAB / ".publish-tmp"
        if dest.exists():
            shutil.rmtree(dest)
        result = run(["git", "clone", "--depth", "1", args.repo, str(dest)],
                     check=False)
        if result.returncode:            # empty repo: clone fails, init instead
            dest.mkdir(parents=True, exist_ok=True)
            run(["git", "init"], cwd=dest)
            run(["git", "remote", "add", "origin", args.repo], cwd=dest)
        cloned = True

    written = build(dest, products)
    print("  " + str(len(written)) + " files -> " + str(dest))
    for f in written:
        print("    " + f)

    if args.dry_run:
        print("\n  dry run -- nothing pushed")
        return 0
    if not cloned:
        return 0

    run(["git", "add", "-A"], cwd=dest)
    if not run(["git", "status", "--porcelain"], cwd=dest).stdout.strip():
        print("\n  product repo already matches this lab commit -- nothing to push")
        return 0
    run(["git", "commit", "-m", "publish from lab " + lab_commit()[:7]], cwd=dest)
    push = run(["git", "push", "-u", "origin", "HEAD:main", "--force"],
               cwd=dest, check=False)
    if push.returncode:
        print(push.stderr, file=sys.stderr)
        raise SystemExit("push failed")
    print("\n  pushed. Apify rebuilds any Actor watching this repo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
