"""Mirror sync storage: pairing, per-peer baselines and offloaded files.

Every reader tolerates a catalog that was opened read-only before it was
migrated to schema v7 (no tables, no meta keys): it reports empty/None.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Dict, Iterable, List, Literal, Optional, Set, Tuple

from smart_gallery.mirror.types import (
    BaselineRow,
    MirrorRow,
    Pairing,
    Peer,
    key,
)
from smart_gallery.organize.filters import FilterOptions

_ROLE_KEY = "mirror_role"
_RULES_KEY = "mirror_offload_rules"
_FACE_SOURCE_KEY = "mirror_face_source"


def _rule_to_json(rule: FilterOptions) -> dict:
    out: dict = {}
    if rule.filetypes:
        out["filetypes"] = list(rule.filetypes)
    if rule.extensions:
        out["extensions"] = list(rule.extensions)
    if rule.date_range:
        start, end = rule.date_range
        out["date_range"] = [
            start.isoformat() if start else None,
            end.isoformat() if end else None,
        ]
    return out


def _rule_from_json(data: dict) -> FilterOptions:
    rng = data.get("date_range")
    date_range = None
    if rng:
        date_range = tuple(date.fromisoformat(v) if v else None for v in rng)
    return FilterOptions(
        filetypes=data.get("filetypes"),
        extensions=data.get("extensions"),
        date_range=date_range,
    )


class MirrorStoreMixin:
    conn: sqlite3.Connection

    def _rows(self, sql: str, params: tuple = ()) -> list:
        """Run a read query; a missing mirror table reads as no rows."""
        try:
            return self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise

    # -- pairing --------------------------------------------------------------
    def get_pairing(self) -> Optional[Pairing]:
        role = self.get_meta(_ROLE_KEY)
        if not role:
            return None
        peers = [
            Peer(r["uuid"], r["db_path"])
            for r in self._rows("SELECT uuid, db_path FROM mirror_peers ORDER BY rowid")
        ]
        raw = self.get_meta(_RULES_KEY)
        rules = [_rule_from_json(d) for d in json.loads(raw)] if raw else []
        return Pairing(role=role, peers=peers, offload_rules=rules)

    def add_peer(self, role: Literal["working", "mirror"], peer: Peer) -> None:
        current = self.get_meta(_ROLE_KEY)
        if current and current != role:
            raise ValueError(f"This catalog is already a {current}, not a {role}.")
        if role == "mirror":
            existing = self._rows("SELECT uuid FROM mirror_peers")
            if any(r["uuid"] != peer.uuid for r in existing):
                raise ValueError("A mirror can be paired with only one working drive.")
        self.conn.execute(
            "INSERT INTO mirror_peers(uuid, db_path) VALUES(?, ?) "
            "ON CONFLICT(uuid) DO UPDATE SET db_path=excluded.db_path",
            (peer.uuid, peer.db_path),
        )
        self.set_meta(_ROLE_KEY, role)

    def remove_peer(self, uuid: str) -> None:
        self.conn.execute("DELETE FROM mirror_baseline WHERE peer_uuid=?", (uuid,))
        self.conn.execute("DELETE FROM mirror_offloaded WHERE holder_uuid=?", (uuid,))
        self.conn.execute("DELETE FROM mirror_peers WHERE uuid=?", (uuid,))
        left = self.conn.execute("SELECT COUNT(*) FROM mirror_peers").fetchone()[0]
        if not left:
            self.conn.execute("DELETE FROM meta WHERE key=?", (_ROLE_KEY,))
        self.conn.commit()

    def set_offload_rules(self, rules: List[FilterOptions]) -> None:
        self.set_meta(_RULES_KEY, json.dumps([_rule_to_json(r) for r in rules]))

    def face_source_uuid(self) -> Optional[str]:
        return self.get_meta(_FACE_SOURCE_KEY)

    def set_face_source_uuid(self, uuid: str) -> None:
        self.set_meta(_FACE_SOURCE_KEY, uuid)

    # -- catalog rows ---------------------------------------------------------
    def mirror_rows(self) -> Dict[str, MirrorRow]:
        return {
            key(r["relpath"]): MirrorRow(
                r["relpath"], r["size_bytes"], r["mtime_ns"], r["content_hash"]
            )
            for r in self._rows(
                "SELECT relpath, size_bytes, mtime_ns, content_hash FROM media_items"
            )
        }

    def set_hashes(self, pairs: Iterable[Tuple[str, str]]) -> None:
        self.conn.executemany(
            "UPDATE media_items SET content_hash=? WHERE relpath=?",
            [(h, rel) for rel, h in pairs],
        )
        self.conn.commit()

    # -- baseline -------------------------------------------------------------
    def baseline(self, peer_uuid: str) -> Dict[str, BaselineRow]:
        return {
            key(r["relpath"]): BaselineRow(
                r["relpath"],
                r["size_bytes"],
                r["working_mtime_ns"],
                r["mirror_mtime_ns"],
            )
            for r in self._rows(
                "SELECT relpath, size_bytes, working_mtime_ns, mirror_mtime_ns "
                "FROM mirror_baseline WHERE peer_uuid=?",
                (peer_uuid,),
            )
        }

    def update_baseline(
        self,
        peer_uuid: str,
        upserts: Iterable[BaselineRow],
        removals: Iterable[str],
    ) -> None:
        self.conn.executemany(
            "DELETE FROM mirror_baseline WHERE peer_uuid=? AND relpath=?",
            [(peer_uuid, rel) for rel in removals],
        )
        self.conn.executemany(
            "INSERT INTO mirror_baseline(peer_uuid, relpath, size_bytes, "
            "working_mtime_ns, mirror_mtime_ns) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(peer_uuid, relpath) DO UPDATE SET "
            "size_bytes=excluded.size_bytes, "
            "working_mtime_ns=excluded.working_mtime_ns, "
            "mirror_mtime_ns=excluded.mirror_mtime_ns",
            [
                (
                    peer_uuid,
                    b.relpath,
                    b.size_bytes,
                    b.working_mtime_ns,
                    b.mirror_mtime_ns,
                )
                for b in upserts
            ],
        )
        self.conn.commit()

    # -- offloaded ------------------------------------------------------------
    def offloaded_relpaths(self) -> Set[str]:
        return {
            r["relpath"] for r in self._rows("SELECT relpath FROM mirror_offloaded")
        }

    def offloaded_on(self, peer_uuid: str) -> Set[str]:
        return {
            r["relpath"]
            for r in self._rows(
                "SELECT relpath FROM mirror_offloaded WHERE holder_uuid=?",
                (peer_uuid,),
            )
        }

    def add_offloaded(self, relpaths: Iterable[str], holder_uuid: str) -> None:
        self.conn.executemany(
            "INSERT OR IGNORE INTO mirror_offloaded(relpath, holder_uuid) "
            "VALUES(?, ?)",
            [(rel, holder_uuid) for rel in relpaths],
        )
        self.conn.commit()

    def remove_offloaded(self, relpaths: Iterable[str]) -> None:
        self.conn.executemany(
            "DELETE FROM mirror_offloaded WHERE relpath=?",
            [(rel,) for rel in relpaths],
        )
        self.conn.commit()
