"""Command line: load SEC insider data, explore the graph, export the public site."""

import argparse
import contextlib
import functools
import http.server
import io
import os
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

from edgar_graph import __version__, codes, export, load, sec, store

DEFAULT_RAW = Path("data/raw")
ENV_FILE = Path(".env")


class CliError(Exception):
    """A problem with how the tool was called, worded for the user."""


def load_env_file(path: Path) -> None:
    """Read KEY=VALUE lines from a .env file. Variables already set in the shell win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="edgar-graph",
        description="Build a public knowledge graph from SEC insider filings (Forms 3, 4, 5).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("EDGAR_GRAPH_DB", "data/edgar-graph.db")),
        help="SQLite graph file (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    ingest = sub.add_parser("ingest", help="download and load quarterly SEC insider data sets")
    ingest.add_argument("quarters", nargs="*", help="quarters such as 2026q3")
    ingest.add_argument("--file", type=Path, help="load a data-set zip you already have")
    ingest.add_argument(
        "--data-dir", type=Path, default=DEFAULT_RAW, help="download cache (default: %(default)s)"
    )
    ingest.add_argument(
        "--user-agent", help=f"your name and email; overrides ${sec.USER_AGENT_ENV}"
    )
    ingest.set_defaults(handler=cmd_ingest)

    sub.add_parser("stats", help="what's loaded").set_defaults(handler=cmd_stats)

    search = sub.add_parser("search", help="find companies, people and funds")
    search.add_argument("text")
    search.add_argument("--limit", type=int, default=15)
    search.set_defaults(handler=cmd_search)

    show = sub.add_parser("show", help="one entity: roles, connections and recent transactions")
    show.add_argument("entity", help="CIK, ticker or exact name")
    show.add_argument("--limit", type=int, default=10, help="transactions to list")
    show.set_defaults(handler=cmd_show)

    exp = sub.add_parser("export", help="write the public static site")
    exp.add_argument("out", nargs="?", type=Path, default=Path("public"))
    exp.set_defaults(handler=cmd_export)

    serve = sub.add_parser("serve", help="preview an exported site on this computer")
    serve.add_argument("dir", nargs="?", type=Path, default=Path("public"))
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(handler=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_env_file(ENV_FILE)
    args = build_parser().parse_args(argv)
    _tolerant_output()
    try:
        return args.handler(args)
    except (CliError, sec.SecError, load.DatasetError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


def cmd_ingest(args: argparse.Namespace) -> int:
    if not args.quarters and not args.file:
        raise CliError(
            "Name at least one quarter, e.g. `edgar-graph ingest 2026q3`, or pass --file"
        )
    try:
        quarters = [sec.parse_quarter(q) for q in args.quarters]
    except ValueError as err:
        raise CliError(str(err)) from err
    jobs: list[tuple[Path, str | None]] = []
    if args.file:
        if not args.file.is_file():
            raise CliError(f"No such file: {args.file}")
        jobs.append((args.file, None))
    client = None
    for quarter in quarters:
        path = args.data_dir / f"{quarter}_form345.zip"
        if not path.exists():
            client = client or sec.SecClient(sec.user_agent(args.user_agent))
            print(f"Downloading {quarter} from sec.gov ...", flush=True)
            path = sec.download_quarter(client, quarter, args.data_dir)
        jobs.append((path, quarter))
    with _db(args.db, create=True) as conn:
        for zip_path, label in jobs:
            result = load.load_quarter(conn, zip_path, label)
            print(
                f"Loaded {result.quarter}: {result.filings:,} filings, "
                f"{result.owners:,} insider reports, {result.transactions:,} transactions"
            )
        stats = store.stats(conn)
    print(
        f"Graph: {stats['companies']:,} companies, {stats['people']:,} people, "
        f"{stats['organizations']:,} funds and other organizations, "
        f"{stats['links']:,} insider links ({args.db})"
    )
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    with _db(args.db) as conn:
        stats = store.stats(conn)
    if not stats["filings"]:
        print("Nothing loaded yet. Run: edgar-graph ingest 2026q3")
        return 0
    print(f"Quarters:      {', '.join(stats['quarters'])}")
    print(f"Filings:       {stats['filings']:,} ({stats['first_filed']} to {stats['last_filed']})")
    print(f"Companies:     {stats['companies']:,}")
    print(f"People:        {stats['people']:,}")
    print(f"Organizations: {stats['organizations']:,}")
    print(f"Insider links: {stats['links']:,}")
    print(f"Transactions:  {stats['transactions']:,}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    with _db(args.db, need_data=True) as conn:
        found = store.search(conn, args.text, args.limit)
    if not found:
        print(f"No company, person or fund matches '{args.text}'.")
        return 0
    for entity, filings in found:
        name = _cut(entity.label(), 48)
        print(f"{entity.cik:>10}  {entity.kind:<12}  {name:<48}  {filings:,} filings")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    with _db(args.db, need_data=True) as conn:
        entity = store.resolve(conn, args.entity)
        if entity is None:
            raise CliError(
                f"No company, person or fund matches '{args.entity}'. "
                f'Try: edgar-graph search "{args.entity}"'
            )
        links = store.links(conn, entity.cik)
        hood = store.neighborhood(conn, entity, links)
        trades, total = store.transactions(conn, entity.cik, limit=args.limit)
        check = store.PriceCheck(conn)

    print(entity.label())
    print(f"{entity.kind}, SEC CIK {entity.cik}, {entity.edgar_url}")
    if entity.name != entity.display:
        print(f"Name as filed: {entity.name}")
    for heading, direction in (("Insiders", "insider"), ("Insider of", "insider_of")):
        rows = [link for link in links if link.direction == direction]
        if rows:
            print(f"\n{heading} ({len(rows)})")
        for link in rows:
            print(
                f"  {_cut(link.other.label(), 34):<34}  {_cut(', '.join(link.roles), 24):<24}  "
                f"{_cut(link.title or '', 28):<28}  {link.first_filed} to {link.last_filed}"
            )
    if hood.ring2:
        heading = (
            "Connected companies (shared insiders)"
            if entity.kind == "company"
            else "Fellow insiders at the same companies"
        )
        more = f", {hood.left_out} more not shown" if hood.left_out else ""
        print(f"\n{heading} ({len(hood.ring2)}{more})")
        via = _via(hood)
        for other in hood.ring2:
            names = via[other.cik]
            more = f" and {len(names) - 2} more" if len(names) > 2 else ""
            print(f"  {_cut(other.label(), 40):<40}  via {', '.join(names[:2])}{more}")
    print(
        f"\nTransactions ({len(trades)} most recent of {total:,})"
        if trades
        else "\nNo transactions."
    )
    for trade in trades:
        who = trade.issuer.label() if trade.issuer.cik != entity.cik else _owners(trade)
        sign = "-" if trade.acq_disp == "D" else "+"
        amount = f"{sign}{_shares(trade.shares)}" + (
            f" @ ${trade.price:,.2f}" if trade.price else ""
        )
        notes = [
            note
            for note, on in (
                ("10b5-1 plan", trade.plan_10b5_1 == 1),
                ("amended", trade.amended),
                (f"derivative: {trade.security}", trade.derivative),
                ("unusual price, check the filing", check.status(trade) == "unusual"),
            )
            if on
        ]
        print(
            f"  {trade.date or '?':<10}  {_cut(who, 30):<30}  {trade.code or '?'} "
            f"{_cut(codes.label(trade.code), 22):<22}  {amount:>26}  {_money(trade.value):>8}  "
            f"{trade.accession}  {', '.join(notes)}".rstrip()
        )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    with _db(args.db, need_data=True) as conn:
        print(f"Exporting to {args.out} ...", flush=True)
        result = export.export_site(
            conn, args.out, progress=lambda done, total: print(f"  {done:,} of {total:,} pages")
        )
    print(f"Wrote {result.files:,} files ({result.bytes / 1e6:.1f} MB) to {result.out_dir}")
    print(f"Preview it: edgar-graph serve {result.out_dir}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    root = args.dir
    if not (root / "index.html").is_file():
        raise CliError(f"No exported site in {root}. Run: edgar-graph export {root}")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError as err:
        raise CliError(f"Can't listen on port {args.port} ({err}). Try --port 8001") from err
    with server:
        print(f"Serving {root} at http://127.0.0.1:{args.port}/  (Ctrl+C to stop)", flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
    return 0


@contextlib.contextmanager
def _db(path: Path, create: bool = False, need_data: bool = False) -> Iterator[sqlite3.Connection]:
    if not create and not path.exists():
        raise CliError(f"No database at {path}. Load data first: edgar-graph ingest 2026q3")
    conn = store.connect(path)
    try:
        if need_data and store.is_empty(conn):
            raise CliError("The database has no filings yet. Run: edgar-graph ingest 2026q3")
        yield conn
    finally:
        conn.close()


def _via(hood: store.Neighborhood) -> dict[int, list[str]]:
    """For each second-ring entity, the first-ring entities that connect it to the center."""
    first = {entity.cik: entity.display for entity in hood.ring1}
    via: dict[int, list[str]] = {entity.cik: [] for entity in hood.ring2}
    for a, b in hood.edges:
        if a in first and b in via:
            via[b].append(first[a])
    return via


def _owners(trade: store.Trade) -> str:
    if not trade.owners:
        return "?"
    extra = f" +{len(trade.owners) - 1}" if len(trade.owners) > 1 else ""
    return trade.owners[0].display + extra


def _cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def _shares(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}"


def _money(value: float | None) -> str:
    if not value:
        return ""
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= size:
            return f"${value / size:.1f}{unit}"
    return f"${value:,.0f}"


def _tolerant_output() -> None:
    """Filers' names can hold characters a Windows console code page can't print."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(errors="replace")
