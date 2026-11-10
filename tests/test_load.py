import zipfile

import pytest
from conftest import ACME, FUND, GLOBEX, JANE, JOHN, RICHARD, SECRET_ADDRESS

from edgar_graph import load, store


def count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_load_builds_entities_filings_roles_and_transactions(db):
    # The 10-K and its owner are skipped; the joint fund filing gives two roles.
    assert (count(db, "filing"), count(db, "role"), count(db, "txn"), count(db, "link")) == (
        6,
        7,
        7,
        6,
    )
    kinds = dict(db.execute("SELECT cik, kind FROM entity").fetchall())
    assert kinds == {
        ACME: "company",
        GLOBEX: "company",
        JANE: "person",
        RICHARD: "person",
        JOHN: "person",
        FUND: "organization",
        5004: "organization",
    }


def test_load_cleans_names_tickers_dates_and_flags(db):
    acme = store.get_entity(db, ACME)
    assert (acme.name, acme.display, acme.ticker) == ("ACME CORP /DE/", "ACME CORP", "ACME")
    assert store.get_entity(db, JOHN).display == "John Q Smith Jr"
    rows = db.execute(
        "SELECT accession, filed, period, plan_10b5_1, remarks FROM filing ORDER BY accession"
    ).fetchall()
    first, sale, amended = rows[0], rows[2], rows[3]
    assert first["filed"] == "2026-08-05" and first["period"] == "2026-08-03"
    assert first["plan_10b5_1"] == 0
    assert sale["plan_10b5_1"] == 1 and sale["remarks"] == 'Sold under a "plan"'
    assert amended["plan_10b5_1"] == 1
    unanswered = db.execute(
        "SELECT plan_10b5_1 FROM filing WHERE accession = '0000005005-26-000006'"
    ).fetchone()[0]
    assert unanswered is None


def test_reloading_a_quarter_replaces_it(db, dataset):
    before = [count(db, table) for table in ("entity", "filing", "role", "txn", "link")]
    load.load_quarter(db, dataset)
    assert [count(db, table) for table in ("entity", "filing", "role", "txn", "link")] == before


def test_street_addresses_are_never_stored(db):
    dump = "\n".join(db.iterdump())
    for secret in SECRET_ADDRESS:
        assert secret not in dump


def test_rejects_files_that_are_not_insider_data_sets(tmp_path, db):
    not_zip = tmp_path / "notes.zip"
    not_zip.write_text("hello")
    with pytest.raises(load.DatasetError, match="not a zip"):
        load.load_quarter(db, not_zip)

    wrong_zip = tmp_path / "other.zip"
    with zipfile.ZipFile(wrong_zip, "w") as archive:
        archive.writestr("README.txt", "nothing here")
    with pytest.raises(load.DatasetError, match="SUBMISSION.tsv is missing"):
        load.load_quarter(db, wrong_zip)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("31-JUL-2026", "2026-07-31"), ("2026-07-31", "2026-07-31"), ("", None), ("soon", None)],
)
def test_iso_date(raw, expected):
    assert load.iso_date(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"), [("1", 1), ("true", 1), ("0", 0), ("false", 0), ("", None)]
)
def test_flag(raw, expected):
    assert load.flag(raw) == expected
