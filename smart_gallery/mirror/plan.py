"""Pure planner: decide what a sync to one mirror does, from catalog rows."""

from __future__ import annotations

from typing import AbstractSet, Callable, Dict, Optional, Set

from smart_gallery.mirror.types import BaselineRow, MirrorPlan, MirrorRow

HashFn = Callable[[str], Optional[str]]


def build_plan(
    working: Dict[str, MirrorRow],
    mirror: Dict[str, MirrorRow],
    baseline: Dict[str, BaselineRow],
    offloaded: Set[str],
    held_here: Set[str],
    hash_working: HashFn,
    hash_mirror: HashFn,
    offloaded_conflict: AbstractSet[str] = frozenset(),
) -> MirrorPlan:
    """Classify every relpath (all dicts/sets keyed by ``key(relpath)``).

    ``offloaded_conflict``: offloaded keys where the working drive now has a
    different file on disk; they are listed, never acted on."""
    plan = MirrorPlan()

    def same_bytes(k: str, w: MirrorRow, m: MirrorRow) -> bool:
        if w.size_bytes != m.size_bytes:
            return False
        if w.mtime_ns == m.mtime_ns:
            return True
        hw = hash_working(k)
        if hw is None:
            return False
        hm = hash_mirror(k)
        return hm is not None and hw == hm

    for k in sorted(set(working) | set(mirror) | set(baseline) | offloaded):
        w = working.get(k)
        m = mirror.get(k)
        b = baseline.get(k)
        is_off = k in offloaded
        if is_off:  # offloaded rows are classified separately
            if k in offloaded_conflict:
                plan.offloaded_conflict.append(w.relpath if w else k)
            _offloaded(plan, k, working.get(k), m, b, k in held_here)
            continue
        if w is None:
            if m is None:
                continue
            if b is None:
                plan.only_on_mirror.append(m.relpath)
            elif (m.size_bytes, m.mtime_ns) != (b.size_bytes, b.mirror_mtime_ns):
                plan.changed_on_mirror.append(m.relpath)  # deleted on E, edited on D
            else:
                plan.trash.append(m.relpath)
            continue
        if m is None:
            plan.copy_new.append(w.relpath)  # new on E, or deleted on D
            continue
        if b is None:
            if same_bytes(k, w, m):
                plan.adopt.append(
                    BaselineRow(w.relpath, w.size_bytes, w.mtime_ns, m.mtime_ns)
                )
            else:
                plan.conflicts.append(w.relpath)
            continue
        e_changed = (w.size_bytes, w.mtime_ns) != (b.size_bytes, b.working_mtime_ns)
        d_changed = (m.size_bytes, m.mtime_ns) != (b.size_bytes, b.mirror_mtime_ns)
        if not e_changed and not d_changed:
            continue
        if same_bytes(k, w, m):
            plan.adopt.append(
                BaselineRow(w.relpath, w.size_bytes, w.mtime_ns, m.mtime_ns)
            )
        elif e_changed and d_changed:
            plan.conflicts.append(w.relpath)
        elif e_changed:
            plan.overwrite.append(w.relpath)
        else:
            plan.changed_on_mirror.append(m.relpath)

    _pair_moves(plan, working, mirror, hash_working, hash_mirror)
    return plan


def _offloaded(
    plan: MirrorPlan,
    k: str,
    e_row: Optional[MirrorRow],
    m: Optional[MirrorRow],
    b: Optional[BaselineRow],
    held_here: bool,
) -> None:
    if m is None:
        if held_here:
            plan.offloaded_missing.append(
                b.relpath if b else (e_row.relpath if e_row else k)
            )
        return
    if b is None:
        if e_row is not None and e_row.size_bytes == m.size_bytes:
            plan.adopt.append(
                BaselineRow(e_row.relpath, e_row.size_bytes, e_row.mtime_ns, m.mtime_ns)
            )
        return
    if (m.size_bytes, m.mtime_ns) != (b.size_bytes, b.mirror_mtime_ns):
        plan.offloaded_changed.append(m.relpath)


def _pair_moves(plan, working, mirror, hash_working, hash_mirror) -> None:
    old_by_key = {r.casefold(): r for r in plan.trash}
    new_by_key = {r.casefold(): r for r in plan.copy_new}
    used_new: Set[str] = set()
    for ok in sorted(old_by_key):
        old = mirror[ok]
        hm = None
        for nk in sorted(new_by_key):
            if nk in used_new or working[nk].size_bytes != old.size_bytes:
                continue
            if hm is None:
                hm = hash_mirror(ok)
                if hm is None:
                    break
            hw = hash_working(nk)
            if hw is not None and hw == hm:
                used_new.add(nk)
                plan.moves.append((old_by_key[ok], new_by_key[nk]))
                break
    moved_old = {o.casefold() for o, _ in plan.moves}
    plan.trash = [r for r in plan.trash if r.casefold() not in moved_old]
    plan.copy_new = [r for r in plan.copy_new if r.casefold() not in used_new]
