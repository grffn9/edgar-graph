"""Load one quarter of the SEC insider-transactions data set into the graph store.

Each quarterly zip holds every Form 3, 4 and 5 filed that quarter as tab-separated tables.
Address columns are never read, so the graph never stores where people live.
"""

import csv
import io
import re
import sqlite3
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from edgar_graph import names, store

FORMS = frozenset({"3", "3/A", "4", "4/A", "5", "5/A"})
_TRADE_COLUMNS = {
    "ACCESSION_NUMBER",
    "SECURITY_TITLE",
    "TRANS_DATE",
    "TRANS_CODE",
    "TRANS_SHARES",
    "TRANS_PRICEPERSHARE",
    "TRANS_ACQUIRED_DISP_CD",
    "SHRS_OWND_FOLWNG_TRANS",
    "DIRECT_INDIRECT_OWNERSHIP",
}
REQUIRED = {
    "SUBMISSION.tsv": {
        "ACCESSION_NUMBER",
        "FILING_DATE",
        "PERIOD_OF_REPORT",
        "DOCUMENT_TYPE",
        "ISSUERCIK",
        "ISSUERNAME",
        "ISSUERTRADINGSYMBOL",
        "REMARKS",
    },
    "REPORTINGOWNER.tsv": {
        "ACCESSION_NUMBER",
        "RPTOWNERCIK",
        "RPTOWNERNAME",
        "RPTOWNER_RELATIONSHIP",
        "RPTOWNER_TITLE",
        "RPTOWNER_TXT",
    },
    "NONDERIV_TRANS.tsv": _TRADE_COLUMNS | {"NONDERIV_TRANS_SK"},
    "DERIV_TRANS.tsv": _TRADE_COLUMNS | {"DERIV_TRANS_SK"},
}
_QUARTER_IN_NAME = re.compile(r"(\d{4}q[1-4])", re.IGNORECASE)


class DatasetError(ValueError):
    """The file isn't a usable SEC insider-transactions data set."""


@dataclass(frozen=True)
class LoadResult:
    quarter: str
    filings: int
    owners: int
    transactions: int


def quarter_label(path: Path) -> str:
    """'2026q3_form345.zip' -> '2026q3'; any other file name is used as is."""
    match = _QUARTER_IN_NAME.search(path.name)
    return match.group(1).lower() if match else path.stem


def iso_date(value: str) -> str | None:
    """'31-JUL-2026' (the data sets' format) or '2026-07-31' -> '2026-07-31'."""
    text = value.strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def flag(value: str) -> int | None:
    """The data sets write booleans as 1/0 or true/false, and leave unanswered ones blank."""
    text = value.strip().lower()
    if text in ("1", "true"):
        return 1
    if text in ("0", "false"):
        return 0
    return None


def load_quarter(conn: sqlite3.Connection, path: Path, quarter: str | None = None) -> LoadResult:
    """Load or reload one quarter's zip. Either all of it lands, or none of it does."""
    quarter = quarter or quarter_label(path)
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as err:
        raise DatasetError(f"{path} is not a zip file") from err
    with archive:
        _check(archive, path)
        filings, issuers = _read_filings(archive)
        roles, owners = _read_owners(archive, filings)
        trades = _read_trades(archive, filings)
    with conn:
        _forget(conn, quarter)
        conn.executemany(store.UPSERT_ENTITY, _entity_rows(issuers, owners))
        conn.executemany(store.INSERT_FILING, [(*filing, quarter) for filing in filings.values()])
        conn.executemany(store.INSERT_ROLE, roles)
        conn.executemany(store.INSERT_TXN, trades)
        store.rebuild_links(conn)
        loaded_at = datetime.now(UTC).isoformat(timespec="seconds")
        conn.execute(
            store.LOG_LOAD, (quarter, path.name, loaded_at, len(filings), len(roles), len(trades))
        )
    return LoadResult(quarter, len(filings), len(roles), len(trades))


def _rows(archive: zipfile.ZipFile, member: str) -> Iterator[dict[str, str]]:
    with archive.open(member) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        # Fields contain literal quote marks, so quoting must be off.
        yield from csv.DictReader(text, delimiter="\t", quoting=csv.QUOTE_NONE)


def _check(archive: zipfile.ZipFile, path: Path) -> None:
    members = set(archive.namelist())
    for member, columns in REQUIRED.items():
        if member not in members:
            raise DatasetError(f"{path.name} isn't an SEC insider data set: {member} is missing")
        with archive.open(member) as raw:
            header = raw.readline().decode("utf-8", errors="replace").rstrip("\r\n").split("\t")
        missing = columns - set(header)
        if missing:
            raise DatasetError(f"{member} in {path.name} lacks {', '.join(sorted(missing))}")


def _keep_latest(seen: dict, cik: int, filed: str, name: str, ticker: str | None = None) -> None:
    """Remember the most recently filed name (and any ticker) for each CIK."""
    current = seen.get(cik)
    if current is None or filed >= current[0]:
        seen[cik] = (
            filed,
            name or (current[1] if current else ""),
            ticker or (current and current[2]),
        )
    elif ticker and not current[2]:
        seen[cik] = (current[0], current[1], ticker)


def _read_filings(archive: zipfile.ZipFile) -> tuple[dict[str, tuple], dict[int, tuple]]:
    filings: dict[str, tuple] = {}
    issuers: dict[int, tuple] = {}
    for row in _rows(archive, "SUBMISSION.tsv"):
        form = row["DOCUMENT_TYPE"].strip()
        filed = iso_date(row["FILING_DATE"])
        cik = row["ISSUERCIK"].strip()
        if form not in FORMS or filed is None or not cik.isdigit():
            continue
        accession = row["ACCESSION_NUMBER"].strip()
        filings[accession] = (
            accession,
            form,
            filed,
            iso_date(row["PERIOD_OF_REPORT"]),
            int(cik),
            flag(row.get("AFF10B5ONE") or ""),  # column added in 2023
            row["REMARKS"].strip() or None,
        )
        ticker = names.clean_ticker(row["ISSUERTRADINGSYMBOL"])
        _keep_latest(issuers, int(cik), filed, row["ISSUERNAME"].strip(), ticker)
    return filings, issuers


def _read_owners(archive: zipfile.ZipFile, filings: dict[str, tuple]) -> tuple[list, dict]:
    roles: list[tuple] = []
    owners: dict[int, tuple] = {}
    for row in _rows(archive, "REPORTINGOWNER.tsv"):
        accession = row["ACCESSION_NUMBER"].strip()
        filing = filings.get(accession)
        cik = row["RPTOWNERCIK"].strip()
        if filing is None or not cik.isdigit():
            continue
        relationship = {part.strip() for part in row["RPTOWNER_RELATIONSHIP"].split(",")}
        title = row["RPTOWNER_TITLE"].strip() or row["RPTOWNER_TXT"].strip() or None
        roles.append(
            (
                accession,
                int(cik),
                int("Director" in relationship),
                int("Officer" in relationship),
                int("TenPercentOwner" in relationship),
                int("Other" in relationship),
                title,
            )
        )
        _keep_latest(owners, int(cik), filing[2], row["RPTOWNERNAME"].strip())
    return roles, owners


def _read_trades(archive: zipfile.ZipFile, filings: dict[str, tuple]) -> list[tuple]:
    trades: list[tuple] = []
    tables = (
        ("NONDERIV_TRANS.tsv", "NONDERIV_TRANS_SK", 0),
        ("DERIV_TRANS.tsv", "DERIV_TRANS_SK", 1),
    )
    for member, key, derivative in tables:
        for row in _rows(archive, member):
            accession = row["ACCESSION_NUMBER"].strip()
            seq = row[key].strip()
            if accession not in filings or not seq.isdigit():
                continue
            trades.append(
                (
                    accession,
                    derivative,
                    int(seq),
                    row["SECURITY_TITLE"].strip() or None,
                    iso_date(row["TRANS_DATE"]),
                    row["TRANS_CODE"].strip() or None,
                    number(row["TRANS_SHARES"]),
                    number(row["TRANS_PRICEPERSHARE"]),
                    row["TRANS_ACQUIRED_DISP_CD"].strip() or None,
                    number(row["SHRS_OWND_FOLWNG_TRANS"]),
                    row["DIRECT_INDIRECT_OWNERSHIP"].strip() or None,
                )
            )
    return trades


def _entity_rows(issuers: dict[int, tuple], owners: dict[int, tuple]) -> list[tuple]:
    rows = []
    for cik, (seen, name, ticker) in issuers.items():
        name = name or f"CIK {cik}"
        rows.append((cik, name, names.display_name(name, "company"), "company", ticker, seen))
    for cik, (seen, name, _) in owners.items():
        if cik in issuers:  # a company that is also an insider elsewhere stays a company
            continue
        name = name or f"CIK {cik}"
        kind = "organization" if names.is_organization(name) else "person"
        rows.append((cik, name, names.display_name(name, kind), kind, None, seen))
    return rows


def _forget(conn: sqlite3.Connection, quarter: str) -> None:
    """Drop an earlier load of the same quarter so reloading never duplicates rows."""
    for table in ("txn", "role"):
        conn.execute(
            f"DELETE FROM {table} WHERE accession IN "
            "(SELECT accession FROM filing WHERE quarter = ?)",
            (quarter,),
        )
    conn.execute("DELETE FROM filing WHERE quarter = ?", (quarter,))
