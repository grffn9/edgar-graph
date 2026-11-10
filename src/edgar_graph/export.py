"""Write the public site: the static app plus one JSON file per company, person or fund."""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from edgar_graph import codes, store

APP_FILES = ("index.html", "app.js", "style.css")
TRADES_PER_ENTITY = 300
FEED_SIZE = 25


@dataclass(frozen=True)
class ExportResult:
    out_dir: Path
    entities: int
    files: int
    bytes: int


def ref(entity: store.Entity) -> list[Any]:
    """Compact pointer to an entity: [cik, display name, kind, ticker]."""
    return [entity.cik, entity.display, entity.kind, entity.ticker]


def trade_json(trade: store.Trade, check: store.PriceCheck) -> dict[str, Any]:
    flags = {"odd": 1} if check.status(trade) == "unusual" else {}
    return {
        **flags,
        "date": trade.date,
        "filed": trade.filed,
        "form": trade.form,
        "code": trade.code,
        "deriv": int(trade.derivative),
        "security": trade.security,
        "shares": trade.shares,
        "price": trade.price,
        "value": trade.value,
        "ad": trade.acq_disp,
        "after": trade.shares_after,
        "own": trade.ownership,
        "plan": trade.plan_10b5_1,
        "acc": trade.accession,
        "url": trade.url,
        "issuer": ref(trade.issuer),
        "owners": [ref(owner) for owner in trade.owners],
    }


def link_json(link: store.Link) -> dict[str, Any]:
    return {
        "ent": ref(link.other),
        "dir": link.direction,
        "roles": list(link.roles),
        "title": link.title,
        "first": link.first_filed,
        "last": link.last_filed,
        "filings": link.filings,
    }


def entity_json(
    conn: sqlite3.Connection, entity: store.Entity, check: store.PriceCheck
) -> dict[str, Any]:
    links = store.links(conn, entity.cik)
    trades, total = store.transactions(conn, entity.cik, limit=TRADES_PER_ENTITY)
    hood = store.neighborhood(conn, entity, links)
    rings = [(entity, 0)] + [(e, 1) for e in hood.ring1] + [(e, 2) for e in hood.ring2]
    return {
        "cik": entity.cik,
        "name": entity.name,
        "display": entity.display,
        "kind": entity.kind,
        "ticker": entity.ticker,
        "edgar": entity.edgar_url,
        "links": [link_json(link) for link in links],
        "tx": [trade_json(trade, check) for trade in trades],
        "tx_total": total,
        "graph": {
            "nodes": [
                {"id": e.cik, "label": e.display, "kind": e.kind, "ring": ring} for e, ring in rings
            ],
            "edges": [list(edge) for edge in hood.edges],
            "left_out": hood.left_out,
        },
    }


class _Writer:
    def __init__(self) -> None:
        self.files = 0
        self.bytes = 0

    def raw(self, path: Path, data: bytes) -> None:
        path.write_bytes(data)
        self.files += 1
        self.bytes += len(data)

    def json(self, path: Path, value: Any) -> None:
        self.raw(path, json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def export_site(
    conn: sqlite3.Connection,
    out_dir: Path,
    progress: Callable[[int, int], None] | None = None,
) -> ExportResult:
    """Write index.html, app.js, style.css and data/ (meta, search index, feed, entities)."""
    out_dir = Path(out_dir)
    data_dir = out_dir / "data"
    (data_dir / "e").mkdir(parents=True, exist_ok=True)
    writer = _Writer()
    app = files("edgar_graph") / "site"
    for name in APP_FILES:
        writer.raw(out_dir / name, (app / name).read_bytes())

    generated = datetime.now(UTC).isoformat(timespec="seconds")
    writer.json(data_dir / "meta.json", {"generated": generated, **store.stats(conn)})
    writer.json(data_dir / "codes.json", codes.as_json())

    entities = store.all_entities(conn)
    activity = store.activity(conn)
    check = store.PriceCheck(conn)
    index = [[*ref(e), activity.get(e.cik, 0)] for e in entities]
    index.sort(key=lambda row: (-row[4], row[1]))  # busiest first, so search shows them first
    writer.json(data_dir / "search.json", index)
    writer.json(
        data_dir / "feed.json",
        {
            "purchases": [
                trade_json(t, check) for t in store.largest_trades(conn, "P", FEED_SIZE, check)
            ],
            "sales": [
                trade_json(t, check) for t in store.largest_trades(conn, "S", FEED_SIZE, check)
            ],
            "directors": [
                {"person": ref(person), "companies": [ref(c) for c in companies]}
                for person, companies in store.top_directors(conn, FEED_SIZE)
            ],
        },
    )
    for done, entity in enumerate(entities, start=1):
        writer.json(data_dir / "e" / f"{entity.cik}.json", entity_json(conn, entity, check))
        if progress and (done % 5000 == 0 or done == len(entities)):
            progress(done, len(entities))
    return ExportResult(out_dir, len(entities), writer.files, writer.bytes)
