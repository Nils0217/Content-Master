"""Layer 2 — HydraDB: durable, queryable home for the graph Cognee builds.

Talks to a local HydraDB graph-node over its HTTP/OpenCypher API
(see hydradb-data/, started per github.com/hydra-db/hydradb's docker quick-start).

IMPORTANT — this HydraDB build (v0.1.0) has a young query engine. Verified live
against the running instance on 2026-09-11:
  * CREATE only supports a single one-hop edge pattern per call:
      CREATE (a:Label {id:<int>, ...})-[:REL]->(b:Label {id:<int>, ...})
    A bare single-node CREATE, or a multi-hop CREATE, is rejected.
  * A node's `id` property MUST be an integer (not a string/UUID).
  * MATCH ... RETURN only supports `<binding>.<property>` or `count(*)` —
    not whole-node projections.
So every helper below stays inside that subset on purpose. `stable_id()`
turns any string key (a product name, a post's slug, ...) into a
deterministic integer id so the rest of the app can keep using readable
string keys.
"""
from __future__ import annotations

import zlib
from typing import Any

import requests

from .config import HydraDBSettings, settings


def stable_id(key: str) -> int:
    """Deterministic string -> positive 31-bit int, for HydraDB's integer-only node ids."""
    return zlib.crc32(key.encode("utf-8")) & 0x7FFFFFFF


def _fmt_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _fmt_props(props: dict[str, Any]) -> str:
    return "{" + ", ".join(f"{k}: {_fmt_value(v)}" for k, v in props.items()) + "}"


class HydraDBClient:
    def __init__(self, cfg: HydraDBSettings | None = None):
        self.cfg = cfg or settings.hydradb

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.cfg.auth_token}",
            "X-Graph-Namespace": self.cfg.namespace,
            "Content-Type": "application/json",
        }

    def raw_query(self, query: str) -> dict[str, Any]:
        resp = requests.post(
            f"{self.cfg.http_url}/v1/graphs/{self.cfg.graph}/query",
            headers=self._headers(),
            json={"cell_id": self.cfg.cell_id, "query": query},
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f"HydraDB query failed: {resp.status_code} {resp.text}\nquery={query}")
        return resp.json()

    # -- writes -----------------------------------------------------------
    def link(
        self,
        a_label: str,
        a_key: str,
        a_props: dict[str, Any],
        rel: str,
        b_label: str,
        b_key: str,
        b_props: dict[str, Any],
    ) -> dict[str, Any]:
        """Create (or re-affirm) a one-hop edge a-[:REL]->b, both nodes upserted by content."""
        a = {"id": stable_id(a_key), **a_props}
        b = {"id": stable_id(b_key), **b_props}
        query = f"CREATE (a:{a_label} {_fmt_props(a)})-[:{rel}]->(b:{b_label} {_fmt_props(b)})"
        return self.raw_query(query)

    # -- reads --------------------------------------------------------------
    def get_property(self, label: str, key: str, prop: str) -> Any:
        node_id = stable_id(key)
        result = self.raw_query(f"MATCH (a:{label} {{id:{node_id}}}) RETURN a.{prop}")
        rows = result.get("rows", [])
        if not rows:
            return None
        return rows[0][0].get("value")

    def neighbors(self, a_label: str, rel: str, b_label: str, b_prop: str) -> list[Any]:
        result = self.raw_query(
            f"MATCH (a:{a_label})-[:{rel}]->(b:{b_label}) RETURN b.{b_prop}"
        )
        return [row[0].get("value") for row in result.get("rows", [])]

    def count_edges(self, a_label: str, rel: str, b_label: str) -> int:
        result = self.raw_query(f"MATCH (a:{a_label})-[:{rel}]->(b:{b_label}) RETURN count(*)")
        rows = result.get("rows", [])
        return rows[0][0]["value"] if rows else 0
