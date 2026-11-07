"""Before/after verification of a FAT32 card update, against an explicit allow-list.

The idea: snapshot the used region of the card before writing, snapshot it
again afterwards, walk both with the read-only parser (via both FAT copies),
and prove that the *only* differences are the ones that were planned:

* every pre-existing file is byte-identical, unless its path is in ``removable``
  (then it must be gone);
* every planned file exists at its destination with the planned SHA-256;
* nothing else appeared;
* the on-disk order of entries in ``ordered_dirs`` is unchanged (some firmware
  stores "recently shown" history as positional indices).
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from .fat32 import Entry, Fat32Volume


@dataclass
class Plan:
    add: dict[str, str]                       # dest path -> sha256
    removable: tuple[str, ...] = ()           # path prefixes allowed to disappear
    new_dirs: frozenset[str] = frozenset()    # directories the plan creates
    ordered_dirs: tuple[str, ...] = ()

    @classmethod
    def from_manifest(cls, text: str, **kw) -> "Plan":
        rows = list(csv.DictReader(io.StringIO(text)))
        add = {r["dest"]: r["sha256"] for r in rows}
        dirs = set()
        for d in add:
            parts = d.strip("/").split("/")[:-1]
            for i in range(1, len(parts) + 1):
                dirs.add("/" + "/".join(parts[:i]))
        return cls(add=add, new_dirs=frozenset(dirs), **kw)


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    unchanged: int = 0
    added: int = 0
    removed: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def walk_both_fats(f, partition_lba: int) -> tuple[list[Entry], list[str]]:
    """Walk via FAT1 and FAT2; return FAT1 entries and any problems (disagreement, anomalies)."""
    problems = []
    trees = []
    for which in (1, 2):
        vol = Fat32Volume(f, partition_lba, follow_fat=which)
        ents = vol.walk()
        if vol.anomalies:
            problems.append(f"FAT{which}: {len(vol.anomalies)} anomalies, e.g. {vol.anomalies[0]}")
        trees.append(ents)
    key = lambda es: sorted((e.path, e.is_dir, e.size, e.sha256) for e in es)  # noqa: E731
    if key(trees[0]) != key(trees[1]):
        problems.append("FAT1 and FAT2 describe different trees")
    return trees[0], problems


def _under(p: str, prefixes) -> bool:
    return any(p == r or p.startswith(r.rstrip("/") + "/") for r in prefixes)


def compare(before: list[Entry], after: list[Entry], plan: Plan) -> Report:
    rep = Report()
    B = {e.path: e for e in before}
    A = {e.path: e for e in after}
    for p, b in B.items():
        a = A.get(p)
        if _under(p, plan.removable):
            if a:
                rep.problems.append(f"NOT REMOVED: {p}")
            else:
                rep.removed += 1
            continue
        if a is None:
            rep.problems.append(f"MISSING: {p}")
        elif not b.is_dir and (a.sha256 != b.sha256 or a.size != b.size):
            rep.problems.append(f"CHANGED: {p}")
        else:
            rep.unchanged += 1
    for p, a in A.items():
        if p in B:
            continue
        if p in plan.add:
            if a.sha256 != plan.add[p]:
                rep.problems.append(f"BAD CONTENT: {p}")
            else:
                rep.added += 1
        elif not (a.is_dir and p in plan.new_dirs):
            rep.problems.append(f"UNEXPECTED: {p}")
    rep.problems += [f"NOT ADDED: {p}" for p in plan.add if p not in A]
    for d in plan.ordered_dirs:
        ob = [e.path for e in before if e.path.rsplit("/", 1)[0] == d]
        oa = [e.path for e in after if e.path.rsplit("/", 1)[0] == d]
        if oa[:len(ob)] != ob:
            rep.problems.append(f"ORDER changed in {d}")
    return rep
