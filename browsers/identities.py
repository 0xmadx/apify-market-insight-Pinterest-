"""Move identities between the vault and a file, so the vault can be emptied
safely and put back.

    python -m browsers.identities export            # vault  -> identities.json
    python -m browsers.identities export --out x.json
    python -m browsers.identities clear             # empty the pool (backs up first)
    python -m browsers.identities restore x.json    # file   -> vault
    python -m browsers.identities show              # what is in the file

WHY THIS EXISTS
---------------
The plan for the stealth branch is: take the cookies AdsPower is holding, turn
AdsPower OFF, and hand those same cookies to DrissionPage / patchright. Every
step of that is reversible EXCEPT losing the cookies, because the cookies are
the accounts. A Pinterest session cannot be regenerated without a human logging
in again, behind the right proxy, and a login is the single most defended thing
Pinterest does.

So `clear` refuses to run without writing a backup first. Not a flag, not a
prompt — a precondition. An operator who wanted the pool gone and got the pool
gone plus their sessions gone has lost the only irreplaceable thing here.

WHAT TRAVELS
------------
The same three things `Identity` is made of, per profile — cookies, user agent,
exit IP — because they are only meaningful together. A jar restored without its
UA would be replayed under a different browser than the one it was born in, and
without its proxy from a different country. Exporting them separately would
make that mistake possible; exporting them as one record makes it hard.

⚠️ THE FILE IS AS SENSITIVE AS A PASSWORD DATABASE. It holds live Pinterest
sessions in the clear and proxy credentials in the clear. It is written to the
repo root, which is gitignored for `*.json` under `browsers/`, but treat it the
way you would treat `.env`: never commit it, never paste it, delete it when the
migration is done.
"""
import argparse
import json
import pathlib
import sys
import time

from src.config import Config
from src.vault import SessionVault

DEFAULT_FILE = "browsers/identities.json"
# Fields that make up an identity. Anything else in the hash is derived state
# the writer will recreate (is_valid) or timing that must NOT be restored stale
# (last_updated is rewritten on restore, see below).
CARRIED = ("cookies_json", "user_agent", "proxy")


def export(vault, platform, out_path):
    """Every profile in the pool, as one JSON document."""
    profiles = []
    for profile_id in sorted(vault.r.smembers(f"valid_profiles:{platform}") or []):
        data = vault.r.hgetall(f"cookie:{platform}:{profile_id}")
        if not data:
            continue
        record = {"profile_id": profile_id}
        record.update({k: data.get(k) for k in CARRIED})
        try:
            record["cookie_count"] = len(json.loads(data.get("cookies_json") or "{}"))
        except (ValueError, TypeError):
            record["cookie_count"] = None
        profiles.append(record)

    document = {
        "exported_at": time.time(),
        "exported_at_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        "platform": platform,
        "profiles": profiles,
    }
    path = pathlib.Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return document, path


def restore(vault, document, platform=None):
    """Put the identities back. Idempotent — restoring twice is restoring once."""
    platform = platform or document.get("platform")
    restored = []
    for record in document.get("profiles") or []:
        profile_id = record["profile_id"]
        fields = {k: record[k] for k in CARRIED if record.get(k)}
        if not fields.get("cookies_json"):
            # A record with no cookies restores nothing usable, and writing it
            # would put a profile in the pool that the vault must then evict.
            continue
        # last_updated is stamped NOW, not carried. The stored value is a
        # freshness claim, and replaying an old one would either hand out a
        # session the vault should have refused as stale, or — worse — make a
        # genuinely stale jar look fresh. The heartbeat means "someone verified
        # this recently", and a restore is not that.
        fields["last_updated"] = str(time.time())
        fields["is_valid"] = "1"
        vault.r.hset(f"cookie:{platform}:{profile_id}", mapping=fields)
        vault.r.sadd(f"valid_profiles:{platform}", profile_id)
        restored.append(profile_id)
    return restored


def clear(vault, platform):
    """Empty the pool. Leaves the cookie hashes; drops pool membership + leases.

    Deliberately NOT a wipe of the hashes. The pool set is what `acquire` reads,
    so removing membership is enough to make the vault empty for a test — and it
    means a mistake costs one `restore`, not a re-login on every account.
    """
    members = list(vault.r.smembers(f"valid_profiles:{platform}") or [])
    for profile_id in members:
        vault.r.srem(f"valid_profiles:{platform}", profile_id)
        vault.r.delete(f"lease:{platform}:{profile_id}")
    return members


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("export", "restore", "clear", "show"))
    ap.add_argument("file", nargs="?", default=DEFAULT_FILE)
    ap.add_argument("--out", default=DEFAULT_FILE)
    ap.add_argument("--platform", default=None)
    args = ap.parse_args()

    config = Config()
    platform = args.platform or config.PLATFORM
    vault = SessionVault(config)
    try:
        vault.r.ping()
    except Exception as exc:
        print(f"cannot reach Redis — {exc}", file=sys.stderr)
        return 2

    if args.action == "show":
        path = pathlib.Path(args.file)
        if not path.exists():
            print(f"no such file: {path}", file=sys.stderr)
            return 1
        document = json.loads(path.read_text(encoding="utf-8"))
        print(f"{path} — exported {document.get('exported_at_human')} "
              f"· platform {document.get('platform')}")
        for record in document.get("profiles") or []:
            print(f"  {record['profile_id']:<24} "
                  f"cookies={record.get('cookie_count')} "
                  f"ua={'yes' if record.get('user_agent') else 'NO'} "
                  f"proxy={(record.get('proxy') or 'none').rsplit('@', 1)[-1]}")
        return 0

    if args.action == "export":
        document, path = export(vault, platform, args.out)
        print(f"exported {len(document['profiles'])} identities -> {path}")
        for record in document["profiles"]:
            print(f"  {record['profile_id']:<24} cookies={record['cookie_count']}")
        print("\n⚠️  This file holds live sessions and proxy passwords in the "
              "clear. Treat it like .env: never commit it, delete it when the "
              "migration is done.")
        return 0

    if args.action == "clear":
        # BACKUP FIRST, ALWAYS. Not a flag and not a prompt — a precondition.
        # The cookies are the accounts, and a login is the one thing that cannot
        # be automated back.
        document, path = export(vault, platform, args.out)
        if not document["profiles"]:
            print("pool is already empty — nothing to back up, nothing to clear")
            return 0
        removed = clear(vault, platform)
        print(f"backed up {len(document['profiles'])} identities -> {path}")
        print(f"cleared {len(removed)} profile(s) from the pool")
        print(f"\nput them back with:\n"
              f"    python -m browsers.identities restore {path}")
        return 0

    path = pathlib.Path(args.file)
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 1
    document = json.loads(path.read_text(encoding="utf-8"))
    restored = restore(vault, document, platform)
    print(f"restored {len(restored)} identities into '{platform}'")
    for profile_id in restored:
        print(f"  {profile_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
