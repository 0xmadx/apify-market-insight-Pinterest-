"""Field inventory: every path in every captured response, docs notwithstanding.

    .venv/Scripts/python.exe -m probes.inventory            # full field listing
    .venv/Scripts/python.exe -m probes.inventory --urls     # only fields carrying URLs/links

The docs describe what the UI *uses*. This enumerates what the wire *returns* —
the gap between the two is where undocumented value hides (external links,
extra IDs, fields the UI never renders).
"""
import glob
import json
import re
import sys


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        if obj:
            yield (path + "[]", f"list({len(obj)})", "")
            yield from walk(obj[0], path + "[]")
        else:
            yield (path + "[]", "EMPTY-LIST", "")
    else:
        yield (path, type(obj).__name__, obj)


def normalise(path):
    out = re.sub(r"(^|\.)\d+(?=\.|$|\[)", r"\1<id>", path)
    out = re.sub(r"\.(nails|family|hallow)(?=\.|$|\[)", r".<term>", out)
    return out


def main():
    urls_only = "--urls" in sys.argv
    for f in sorted(glob.glob("probes/results/*.json")):
        data = json.load(open(f, encoding="utf-8"))
        if isinstance(data, dict) and "resource_response" in data:
            data = data["resource_response"].get("data")
        seen = {}
        for path, typ, sample in walk(data):
            norm = normalise(path)
            if norm not in seen:
                seen[norm] = (typ, sample)
        rows = []
        for k in sorted(seen):
            typ, sample = seen[k]
            s = str(sample)
            if urls_only and not ("http" in s or "url" in k.lower() or "link" in k.lower()):
                continue
            if len(s) > 72:
                s = s[:72] + "..."
            rows.append("  %-72s %-10s %s" % (k, typ, s))
        if rows:
            print("\n=== %s ===" % f.replace("\\", "/").split("/")[-1])
            print("\n".join(rows))


if __name__ == "__main__":
    main()
