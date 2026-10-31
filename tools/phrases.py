#!/usr/bin/env python3
"""The phrases the app shows, and what each language's pack lacks.

    python3 tools/phrases.py                    # every pack: counts, gaps
    python3 tools/phrases.py --check            # exit 1 on a gap (tests)
    python3 tools/phrases.py --missing zh-CN    # a pack's gaps, as JSON
    python3 tools/phrases.py --merge zh-CN todo.json   # add translations

A phrase is the English text of a tr(), trn() or N_() call in main.py
(v15.1, see "languages" there). A pack, data/lang/<code>.json, maps each
English phrase to its translation. A pack has a gap when it lacks a phrase,
keeps one the app no longer shows, or names {fields} its English does not:
the app would show English for the first, and would fail on the last.

To translate: take a pack's gaps with --missing, fill in each "", and
--merge the file back. Entries left "" are skipped. The pack is written in
the order the phrases appear in main.py, so a change to it reads as a
change to the app.
"""
import ast
import json
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANG = ROOT / "data" / "lang"


def literals(node: ast.AST):
    """The strings an argument can be: a literal, or either side of a
    choice between literals (tr("Mute" if on else "Unmute"))."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node
    elif isinstance(node, ast.IfExp):
        yield from literals(node.body)
        yield from literals(node.orelse)


def phrases(source: Path = ROOT / "main.py") -> list:
    """Every phrase, once, in the order main.py shows them."""
    seen = {}
    tree = ast.parse(source.read_text("utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None)
        count = {"tr": 1, "N_": 1, "trn": 2}.get(name, 0)
        for arg in node.args[:count]:
            for text in literals(arg):
                seen.setdefault(text.value, (text.lineno, text.col_offset))
    return sorted(seen, key=seen.get)


def fields(text: str) -> set:
    return {name for _lit, name, _spec, _conv in
            string.Formatter().parse(text) if name is not None}


def gaps(pack: dict, catalogue: list) -> dict:
    known = set(catalogue)
    return {
        "missing": [p for p in catalogue if p not in pack],
        "stale": [p for p in pack if p not in known],
        "fields": [p for p in catalogue if p in pack and
                   fields(pack[p]) != fields(p)],
    }


def packs() -> dict:
    return {path.stem: json.loads(path.read_text("utf-8"))
            for path in sorted(LANG.glob("*.json"))}


def write(code: str, pack: dict, catalogue: list) -> None:
    order = {p: i for i, p in enumerate(catalogue)}
    ordered = dict(sorted(pack.items(),
                          key=lambda kv: order.get(kv[0], len(order))))
    (LANG / f"{code}.json").write_text(
        json.dumps(ordered, ensure_ascii=False, indent=1) + "\n", "utf-8")


def main() -> int:
    catalogue = phrases()
    args = sys.argv[1:]
    if args[:1] == ["--missing"]:
        pack = packs().get(args[1], {})
        print(json.dumps({p: "" for p in gaps(pack, catalogue)["missing"]},
                         ensure_ascii=False, indent=1))
        return 0
    if args[:1] == ["--merge"]:
        code, todo = args[1], json.loads(Path(args[2]).read_text("utf-8"))
        pack = packs().get(code, {})
        pack.update({k: v for k, v in todo.items() if v})
        write(code, pack, catalogue)
        args = []
    print(f"{len(catalogue)} phrases in main.py")
    bad = 0
    for code, pack in packs().items():
        found = gaps(pack, catalogue)
        bad += sum(map(len, found.values()))
        print(f"  {code}: {len(pack)} translated; " + ", ".join(
            f"{len(v)} {k}" for k, v in found.items()))
        for kind, items in found.items():
            for phrase in items[:5]:
                print(f"      {kind}: {phrase[:70]!r}")
    return 1 if bad and "--check" in args else 0


if __name__ == "__main__":
    sys.exit(main())
