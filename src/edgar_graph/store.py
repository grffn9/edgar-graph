"""The graph store: SQLite tables plus the queries the CLI and the site export share.

Nodes are entities (companies, people, organizations) keyed by the SEC's CIK. Edges are the
insider roles reported in filings, rolled up into one `link` row per owner and company. Every
fact keeps its filing's accession number, so it can always be traced back to sec.gov.
"""

import sqlite3
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from itertools import batched
from pathlib import Path
from typing import Any

from edgar_graph.names import ticker_symbols

SCHEMA = """
CREATE TABLE IF NOT EXISTS entity (
    cik       INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,  -- as filed
    display   TEXT NOT NULL,  -- reader-friendly
    kind      TEXT NOT NULL CHECK (kind IN ('company', 'person', 'organization')),
    ticker    TEXT,
    name_seen TEXT NOT NULL   -- filing date the name was last seen
);
CREATE TABLE IF NOT EXISTS filing (
    accession   TEXT PRIMARY KEY,
    form        TEXT NOT NULL,
    filed       TEXT NOT NULL,
    period      TEXT,
    issuer_cik  INTEGER NOT NULL REFERENCES entity (cik),
    plan_10b5_1 INTEGER,      -- the Rule 10b5-1 checkbox (2023+); NULL when not answered
    remarks     TEXT,
    quarter     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS role (
    accession TEXT NOT NULL REFERENCES filing (accession),
    owner_cik INTEGER NOT NULL REFERENCES entity (cik),
    director  INTEGER NOT NULL,
    officer   INTEGER NOT NULL,
    ten_pct   INTEGER NOT NULL,
    other     INTEGER NOT NULL,
    title     TEXT,
    PRIMARY KEY (accession, owner_cik)
);
CREATE TABLE IF NOT EXISTS txn (
    accession    TEXT NOT NULL REFERENCES filing (accession),
    derivative   INTEGER NOT NULL,
    seq          INTEGER NOT NULL,
    security     TEXT,
    date         TEXT,
    code         TEXT,
    shares       REAL,
    price        REAL,
    acq_disp     TEXT,
    shares_after REAL,
    ownership    TEXT,
    PRIMARY KEY (accession, derivative, seq)
);
CREATE TABLE IF NOT EXISTS link (  -- rebuilt from role + filing after every load
    owner_cik   INTEGER NOT NULL,
    issuer_cik  INTEGER NOT NULL,
    director    INTEGER NOT NULL,
    officer     INTEGER NOT NULL,
    ten_pct     INTEGER NOT NULL,
    other       INTEGER NOT NULL,
    title       TEXT,
    first_filed TEXT NOT NULL,
    last_filed  TEXT NOT NULL,
    filings     INTEGER NOT NULL,
    PRIMARY KEY (owner_cik, issuer_cik)
);
CREATE TABLE IF NOT EXISTS load_log (
    quarter      TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    loaded_at    TEXT NOT NULL,
    filings      INTEGER NOT NULL,
    owners       INTEGER NOT NULL,
    transactions INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS filing_issuer ON filing (issuer_cik);
CREATE INDEX IF NOT EXISTS filing_quarter ON filing (quarter);
CREATE INDEX IF NOT EXISTS role_owner ON role (owner_cik);
CREATE INDEX IF NOT EXISTS link_issuer ON link (issuer_cik);
"""

UPSERT_ENTITY = """
INSERT INTO entity (cik, name, display, kind, ticker, name_seen) VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (cik) DO UPDATE SET
    kind = CASE WHEN 'company' IN (entity.kind, excluded.kind) THEN 'company'
                ELSE excluded.kind END,
    name = CASE WHEN excluded.name_seen >= entity.name_seen THEN excluded.name
                ELSE entity.name END,
    display = CASE WHEN excluded.name_seen >= entity.name_seen THEN excluded.display
                   ELSE entity.display END,
    ticker = COALESCE(CASE WHEN excluded.name_seen >= entity.name_seen THEN excluded.ticker END,
                      entity.ticker, excluded.ticker),
    name_seen = MAX(entity.name_seen, excluded.name_seen)
"""
INSERT_FILING = """
INSERT OR REPLACE INTO filing
    (accession, form, filed, period, issuer_cik, plan_10b5_1, remarks, quarter)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""
INSERT_ROLE = """
INSERT OR REPLACE INTO role (accession, owner_cik, director, officer, ten_pct, other, title)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""
INSERT_TXN = """
INSERT OR REPLACE INTO txn (accession, derivative, seq, security, date, code, shares, price,
                            acq_disp, shares_after, ownership)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
LOG_LOAD = """
INSERT OR REPLACE INTO load_log (quarter, source, loaded_at, filings, owners, transactions)
VALUES (?, ?, ?, ?, ?, ?)
"""
_REBUILD_LINKS = """
INSERT INTO link (owner_cik, issuer_cik, director, officer, ten_pct, other, title,
                  first_filed, last_filed, filings)
SELECT r.owner_cik, f.issuer_cik,
       MAX(r.director), MAX(r.officer), MAX(r.ten_pct), MAX(r.other),
       (SELECT r2.title FROM role r2 JOIN filing f2 ON f2.accession = r2.accession
         WHERE r2.owner_cik = r.owner_cik AND f2.issuer_cik = f.issuer_cik
           AND r2.title IS NOT NULL
         ORDER BY f2.filed DESC LIMIT 1),
       MIN(f.filed), MAX(f.filed), COUNT(*)
  FROM role r JOIN filing f ON f.accession = r.accession
 WHERE r.owner_cik <> f.issuer_cik
 GROUP BY r.owner_cik, f.issuer_cik
"""

_ENTITY = "e.cik, e.name, e.display, e.kind, e.ticker"
_LINKS = f"""
SELECT {_ENTITY}, l.director, l.officer, l.ten_pct, l.other, l.title,
       l.first_filed, l.last_filed, l.filings, 'insider' AS direction
  FROM link l JOIN entity e ON e.cik = l.owner_cik
 WHERE l.issuer_cik = :cik
UNION ALL
SELECT {_ENTITY}, l.director, l.officer, l.ten_pct, l.other, l.title,
       l.first_filed, l.last_filed, l.filings, 'insider_of' AS direction
  FROM link l JOIN entity e ON e.cik = l.issuer_cik
 WHERE l.owner_cik = :cik
 ORDER BY last_filed DESC, filings DESC, display
"""
_TRADE = """
t.accession, t.derivative, t.security, t.date, t.code, t.shares, t.price, t.acq_disp,
t.shares_after, t.ownership, f.form, f.filed, f.plan_10b5_1,
i.cik AS i_cik, i.name AS i_name, i.display AS i_display, i.kind AS i_kind, i.ticker AS i_ticker
"""
_TRADE_FROM = """
txn t JOIN filing f ON f.accession = t.accession JOIN entity i ON i.cik = f.issuer_cik
"""
_TRADES_OF = """
t.accession IN (SELECT accession FROM filing WHERE issuer_cik = :cik
                UNION SELECT accession FROM role WHERE owner_cik = :cik)
"""
ROLE_LABELS = (
    ("director", "Director"),
    ("officer", "Officer"),
    ("ten_pct", "10% owner"),
    ("other", "Other insider"),
)


@dataclass(frozen=True)
class Entity:
    cik: int
    name: str
    display: str
    kind: str
    ticker: str | None = None

    @property
    def edgar_url(self) -> str:
        return f"https://www.sec.gov/edgar/browse/?CIK={self.cik}"

    def label(self) -> str:
        return f"{self.display} ({self.ticker})" if self.ticker else self.display


@dataclass(frozen=True)
class Link:
    """One edge of the graph, seen from the entity it was looked up for."""

    other: Entity
    direction: str  # "insider": other is an insider of this entity; "insider_of": the reverse
    roles: tuple[str, ...]
    title: str | None
    first_filed: str
    last_filed: str
    filings: int


@dataclass(frozen=True)
class Trade:
    accession: str
    form: str
    filed: str
    date: str | None
    code: str | None
    derivative: bool
    security: str | None
    shares: float | None
    price: float | None
    acq_disp: str | None
    shares_after: float | None
    ownership: str | None
    plan_10b5_1: int | None
    issuer: Entity
    owners: tuple[Entity, ...] = ()

    @property
    def value(self) -> float | None:
        if self.shares is None or not self.price:
            return None
        return self.shares * self.price

    @property
    def amended(self) -> bool:
        return self.form.endswith("/A")

    @property
    def url(self) -> str:
        return filing_url(self.issuer.cik, self.accession)


@dataclass(frozen=True)
class Neighborhood:
    """Two hops out from an entity: its links, then their links (capped for drawing)."""

    center: Entity
    ring1: tuple[Entity, ...]
    ring2: tuple[Entity, ...]
    edges: tuple[tuple[int, int], ...]
    left_out: int


def filing_url(cik: int, accession: str) -> str:
    """The filing's index page on sec.gov."""
    folder = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{accession}-index.htm"


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def rebuild_links(conn: sqlite3.Connection) -> None:
    """Roll per-filing roles up into one link per owner and company (call inside a load)."""
    conn.execute("DELETE FROM link")
    conn.execute(_REBUILD_LINKS)


def is_empty(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM filing LIMIT 1").fetchone() is None


def _entity(row: sqlite3.Row, prefix: str = "") -> Entity:
    return Entity(
        row[f"{prefix}cik"],
        row[f"{prefix}name"],
        row[f"{prefix}display"],
        row[f"{prefix}kind"],
        row[f"{prefix}ticker"],
    )


def get_entity(conn: sqlite3.Connection, cik: int) -> Entity | None:
    row = conn.execute(f"SELECT {_ENTITY} FROM entity e WHERE e.cik = ?", (cik,)).fetchone()
    return _entity(row) if row else None


def _entities(conn: sqlite3.Connection, ciks: Iterable[int]) -> dict[int, Entity]:
    found: dict[int, Entity] = {}
    for chunk in batched(ciks, 500):
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(f"SELECT {_ENTITY} FROM entity e WHERE e.cik IN ({marks})", chunk):
            found[row["cik"]] = _entity(row)
    return found


def all_entities(conn: sqlite3.Connection) -> list[Entity]:
    return [_entity(row) for row in conn.execute(f"SELECT {_ENTITY} FROM entity e ORDER BY cik")]


def resolve(conn: sqlite3.Connection, ident: str) -> Entity | None:
    """Find an entity by CIK, ticker, or exact name (as filed or as displayed)."""
    text = ident.strip()
    if text.isdigit():
        return get_entity(conn, int(text))
    symbol = text.upper()
    candidates = conn.execute(
        f"""SELECT {_ENTITY},
                   (SELECT MAX(filed) FROM filing f WHERE f.issuer_cik = e.cik) AS last
              FROM entity e
             WHERE e.ticker IS NOT NULL AND instr(upper(e.ticker), ?) > 0
             ORDER BY last DESC""",
        (symbol,),
    ).fetchall()
    for row in candidates:
        if symbol in ticker_symbols(row["ticker"]):
            return _entity(row)
    row = conn.execute(
        f"""SELECT {_ENTITY} FROM entity e
             WHERE lower(e.display) = lower(:t) OR lower(e.name) = lower(:t)
             ORDER BY e.kind = 'company' DESC LIMIT 1""",
        {"t": text},
    ).fetchone()
    return _entity(row) if row else None


def search(conn: sqlite3.Connection, text: str, limit: int = 15) -> list[tuple[Entity, int]]:
    """Entities whose name or ticker contains every word, busiest first, with filing counts."""
    words = text.lower().split()
    if not words:
        return []
    match = "(lower(e.display) LIKE ? OR lower(e.name) LIKE ? OR lower(e.ticker) LIKE ?)"
    params = [pattern for word in words for pattern in [f"%{word}%"] * 3]
    rows = conn.execute(
        f"""SELECT {_ENTITY},
                   (SELECT COUNT(*) FROM filing f WHERE f.issuer_cik = e.cik)
                   + (SELECT COUNT(*) FROM role r WHERE r.owner_cik = e.cik) AS activity
              FROM entity e
             WHERE {" AND ".join([match] * len(words))}
             ORDER BY upper(coalesce(e.ticker, '')) = upper(?) DESC, activity DESC, e.display
             LIMIT ?""",
        (*params, text.strip(), limit),
    ).fetchall()
    return [(_entity(row), row["activity"]) for row in rows]


def links(conn: sqlite3.Connection, cik: int) -> list[Link]:
    """Every insider relationship touching this entity, most recent first."""
    return [
        Link(
            other=_entity(row),
            direction=row["direction"],
            roles=tuple(name for column, name in ROLE_LABELS if row[column]),
            title=row["title"],
            first_filed=row["first_filed"],
            last_filed=row["last_filed"],
            filings=row["filings"],
        )
        for row in conn.execute(_LINKS, {"cik": cik})
    ]


def _owners_by_filing(conn: sqlite3.Connection, accessions: Iterable[str]) -> dict[str, list]:
    owners: dict[str, list[Entity]] = defaultdict(list)
    for chunk in batched(sorted(set(accessions)), 500):
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"""SELECT r.accession, {_ENTITY} FROM role r JOIN entity e ON e.cik = r.owner_cik
                 WHERE r.accession IN ({marks}) ORDER BY r.accession, e.display""",
            chunk,
        ):
            owners[row["accession"]].append(_entity(row))
    return owners


def _trades(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[Trade]:
    owners = _owners_by_filing(conn, (row["accession"] for row in rows))
    return [
        Trade(
            accession=row["accession"],
            form=row["form"],
            filed=row["filed"],
            date=row["date"],
            code=row["code"],
            derivative=bool(row["derivative"]),
            security=row["security"],
            shares=row["shares"],
            price=row["price"],
            acq_disp=row["acq_disp"],
            shares_after=row["shares_after"],
            ownership=row["ownership"],
            plan_10b5_1=row["plan_10b5_1"],
            issuer=_entity(row, "i_"),
            owners=tuple(owners.get(row["accession"], ())),
        )
        for row in rows
    ]


def transactions(
    conn: sqlite3.Connection, cik: int, limit: int | None = 50
) -> tuple[list[Trade], int]:
    """Transactions in filings about this company or by this insider: (latest first, total)."""
    params = {"cik": cik, "limit": -1 if limit is None else limit}
    total = conn.execute(f"SELECT COUNT(*) FROM txn t WHERE {_TRADES_OF}", params).fetchone()[0]
    rows = conn.execute(
        f"""SELECT {_TRADE} FROM {_TRADE_FROM} WHERE {_TRADES_OF}
             ORDER BY t.date DESC, f.filed DESC, t.accession, t.derivative, t.seq
             LIMIT :limit""",
        params,
    ).fetchall()
    return _trades(conn, rows), total


def neighborhood(
    conn: sqlite3.Connection,
    center: Entity,
    first: list[Link],
    ring1_cap: int = 20,
    ring2_cap: int = 24,
) -> Neighborhood:
    """Company -> its insiders -> their other companies; person -> companies -> co-insiders."""
    ring1 = [link.other for link in first[:ring1_cap]]
    ids = [entity.cik for entity in ring1]
    edges = [(center.cik, cik) for cik in ids]
    left_out = max(0, len(first) - ring1_cap)
    ring2: list[Entity] = []
    if ids:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""SELECT other, COUNT(DISTINCT via) AS shared, MAX(last_filed) AS last FROM (
                    SELECT issuer_cik AS other, owner_cik AS via, last_filed
                      FROM link WHERE owner_cik IN ({marks})
                    UNION ALL
                    SELECT owner_cik AS other, issuer_cik AS via, last_filed
                      FROM link WHERE issuer_cik IN ({marks}))
                 WHERE other <> ? AND other NOT IN ({marks})
                 GROUP BY other ORDER BY shared DESC, last DESC""",
            (*ids, *ids, center.cik, *ids),
        ).fetchall()
        left_out += max(0, len(rows) - ring2_cap)
        keep = [row["other"] for row in rows[:ring2_cap]]
        if keep:
            kept = ",".join("?" * len(keep))
            pairs = conn.execute(
                f"""SELECT owner_cik AS a, issuer_cik AS b FROM link
                     WHERE owner_cik IN ({marks}) AND issuer_cik IN ({kept})
                    UNION
                    SELECT issuer_cik AS a, owner_cik AS b FROM link
                     WHERE issuer_cik IN ({marks}) AND owner_cik IN ({kept})""",
                (*ids, *keep, *ids, *keep),
            ).fetchall()
            edges += [(row["a"], row["b"]) for row in pairs]
            found = _entities(conn, keep)
            ring2 = [found[cik] for cik in keep if cik in found]
    return Neighborhood(center, tuple(ring1), tuple(ring2), tuple(edges), left_out)


def top_directors(conn: sqlite3.Connection, limit: int = 25) -> list[tuple[Entity, list[Entity]]]:
    """People who reported as a director at more than one company, most boards first."""
    people = conn.execute(
        f"""SELECT {_ENTITY}, COUNT(*) AS boards
              FROM link l JOIN entity e ON e.cik = l.owner_cik
             WHERE l.director = 1 AND e.kind = 'person'
             GROUP BY e.cik HAVING boards > 1
             ORDER BY boards DESC, e.display LIMIT ?""",
        (limit,),
    ).fetchall()
    return [
        (
            _entity(person),
            [
                _entity(row)
                for row in conn.execute(
                    f"""SELECT {_ENTITY} FROM link l JOIN entity e ON e.cik = l.issuer_cik
                         WHERE l.owner_cik = ? AND l.director = 1 ORDER BY e.display""",
                    (person["cik"],),
                )
            ],
        )
        for person in people
    ]


class PriceCheck:
    """Compares a trade's price with what other insiders of the same company reported.

    Prices in filings are typed by hand, and a total typed into the price box can turn a
    $70,000 sale into a $7 billion one. A price is "ok" within 3x of the median of at least
    three other filers' prices, "unusual" outside it, and "unverified" with fewer to compare.
    """

    MIN_OTHERS = 3
    TOLERANCE = 3.0

    def __init__(self, conn: sqlite3.Connection) -> None:
        prices: dict[int, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
        for issuer, owner, price in conn.execute(
            """SELECT f.issuer_cik, r.owner_cik, t.price
                 FROM txn t JOIN filing f ON f.accession = t.accession
                 JOIN role r ON r.accession = t.accession
                WHERE t.derivative = 0 AND t.price > 0"""
        ):
            prices[issuer][owner].append(price)
        self._medians = {
            issuer: {owner: statistics.median(values) for owner, values in owners.items()}
            for issuer, owners in prices.items()
        }

    def status(self, trade: Trade) -> str:
        if trade.derivative or not trade.price:
            return "n/a"
        own = {owner.cik for owner in trade.owners}
        others = [
            median
            for owner, median in self._medians.get(trade.issuer.cik, {}).items()
            if owner not in own
        ]
        if len(others) < self.MIN_OTHERS:
            return "unverified"
        typical = statistics.median(others)
        within = typical / self.TOLERANCE <= trade.price <= typical * self.TOLERANCE
        return "ok" if within else "unusual"


def largest_trades(
    conn: sqlite3.Connection, code: str, limit: int = 25, check: PriceCheck | None = None
) -> list[Trade]:
    """Biggest non-derivative trades with this code by dollar value.

    Amendments are left out. One block reported in several filings (a filing sent twice, or
    a fund and its manager each reporting the same shares) is listed once, with every filer.
    With a PriceCheck, only trades whose price checks out are kept.
    """
    rows = conn.execute(
        f"""SELECT {_TRADE} FROM {_TRADE_FROM}
             WHERE t.code = ? AND t.derivative = 0 AND f.form NOT LIKE '%/A'
               AND t.shares > 0 AND t.price > 0
             ORDER BY t.shares * t.price DESC LIMIT ?""",
        (code, limit * 20),
    ).fetchall()
    picked: list[Trade] = []
    position: dict[tuple, int] = {}
    for trade in _trades(conn, rows):
        if check and check.status(trade) != "ok":
            continue
        key = (trade.issuer.cik, trade.date, trade.shares, trade.price)
        if key in position:
            first = picked[position[key]]
            extra = tuple(owner for owner in trade.owners if owner not in first.owners)
            picked[position[key]] = replace(first, owners=first.owners + extra)
        elif len(picked) < limit:
            position[key] = len(picked)
            picked.append(trade)
        else:
            break
    return picked


def activity(conn: sqlite3.Connection) -> dict[int, int]:
    """How many filings each entity appears in, as company or as insider."""
    counts: dict[int, int] = defaultdict(int)
    for cik, n in conn.execute("SELECT issuer_cik, COUNT(*) FROM filing GROUP BY issuer_cik"):
        counts[cik] += n
    for cik, n in conn.execute("SELECT owner_cik, COUNT(*) FROM role GROUP BY owner_cik"):
        counts[cik] += n
    return counts


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM entity GROUP BY kind").fetchall())
    first, last = conn.execute("SELECT MIN(filed), MAX(filed) FROM filing").fetchone()

    def count(table: str) -> int:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    return {
        "quarters": [row[0] for row in conn.execute("SELECT quarter FROM load_log ORDER BY 1")],
        "companies": kinds.get("company", 0),
        "people": kinds.get("person", 0),
        "organizations": kinds.get("organization", 0),
        "filings": count("filing"),
        "transactions": count("txn"),
        "links": count("link"),
        "first_filed": first,
        "last_filed": last,
    }
