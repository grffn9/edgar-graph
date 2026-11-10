"""A small, hand-written insider data set in the SEC's real file layout.

Two companies (ACME, Globex), one director who sits on both boards (the interlock), a CFO
whose sale was later amended, a fund and its general partner filing jointly, and a new CEO
whose Form 3 lists holdings only. Street addresses are included so tests can prove they
never reach the database or the exported site.
"""

import zipfile
from pathlib import Path

import pytest

from edgar_graph import load, store

COLUMNS = {
    "SUBMISSION.tsv": (
        "ACCESSION_NUMBER FILING_DATE PERIOD_OF_REPORT DATE_OF_ORIG_SUB NO_SECURITIES_OWNED "
        "NOT_SUBJECT_SEC16 FORM3_HOLDINGS_REPORTED FORM4_TRANS_REPORTED DOCUMENT_TYPE ISSUERCIK "
        "ISSUERNAME ISSUERTRADINGSYMBOL REMARKS AFF10B5ONE"
    ),
    "REPORTINGOWNER.tsv": (
        "ACCESSION_NUMBER RPTOWNERCIK RPTOWNERNAME RPTOWNER_RELATIONSHIP RPTOWNER_TITLE "
        "RPTOWNER_TXT RPTOWNER_STREET1 RPTOWNER_STREET2 RPTOWNER_CITY RPTOWNER_STATE "
        "RPTOWNER_ZIPCODE RPTOWNER_STATE_DESC FILE_NUMBER"
    ),
    "NONDERIV_TRANS.tsv": (
        "ACCESSION_NUMBER NONDERIV_TRANS_SK SECURITY_TITLE SECURITY_TITLE_FN TRANS_DATE "
        "TRANS_DATE_FN DEEMED_EXECUTION_DATE DEEMED_EXECUTION_DATE_FN TRANS_FORM_TYPE TRANS_CODE "
        "EQUITY_SWAP_INVOLVED EQUITY_SWAP_TRANS_CD_FN TRANS_TIMELINESS TRANS_TIMELINESS_FN "
        "TRANS_SHARES TRANS_SHARES_FN TRANS_PRICEPERSHARE TRANS_PRICEPERSHARE_FN "
        "TRANS_ACQUIRED_DISP_CD TRANS_ACQUIRED_DISP_CD_FN SHRS_OWND_FOLWNG_TRANS "
        "SHRS_OWND_FOLWNG_TRANS_FN VALU_OWND_FOLWNG_TRANS VALU_OWND_FOLWNG_TRANS_FN "
        "DIRECT_INDIRECT_OWNERSHIP DIRECT_INDIRECT_OWNERSHIP_FN NATURE_OF_OWNERSHIP "
        "NATURE_OF_OWNERSHIP_FN"
    ),
    "DERIV_TRANS.tsv": (
        "ACCESSION_NUMBER DERIV_TRANS_SK SECURITY_TITLE SECURITY_TITLE_FN CONV_EXERCISE_PRICE "
        "CONV_EXERCISE_PRICE_FN TRANS_DATE TRANS_DATE_FN DEEMED_EXECUTION_DATE "
        "DEEMED_EXECUTION_DATE_FN TRANS_FORM_TYPE TRANS_CODE EQUITY_SWAP_INVOLVED "
        "EQUITY_SWAP_TRANS_CD_FN TRANS_TIMELINESS TRANS_TIMELINESS_FN TRANS_SHARES "
        "TRANS_SHARES_FN TRANS_TOTAL_VALUE TRANS_TOTAL_VALUE_FN TRANS_PRICEPERSHARE "
        "TRANS_PRICEPERSHARE_FN TRANS_ACQUIRED_DISP_CD TRANS_ACQUIRED_DISP_CD_FN EXCERCISE_DATE "
        "EXCERCISE_DATE_FN EXPIRATION_DATE EXPIRATION_DATE_FN UNDLYNG_SEC_TITLE "
        "UNDLYNG_SEC_TITLE_FN UNDLYNG_SEC_SHARES UNDLYNG_SEC_SHARES_FN UNDLYNG_SEC_VALUE "
        "UNDLYNG_SEC_VALUE_FN SHRS_OWND_FOLWNG_TRANS SHRS_OWND_FOLWNG_TRANS_FN "
        "VALU_OWND_FOLWNG_TRANS VALU_OWND_FOLWNG_TRANS_FN DIRECT_INDIRECT_OWNERSHIP "
        "DIRECT_INDIRECT_OWNERSHIP_FN NATURE_OF_OWNERSHIP NATURE_OF_OWNERSHIP_FN"
    ),
}

ACME, GLOBEX = 1000, 2000
JANE, RICHARD, FUND, FUND_GP, JOHN = 5001, 5002, 5003, 5004, 5005
SECRET_ADDRESS = ("1 SECRET LANE", "HIDDENVILLE", "90210")


def _acme(**row):
    return {
        "ISSUERCIK": "0000001000",
        "ISSUERNAME": "ACME CORP /DE/",
        "ISSUERTRADINGSYMBOL": "NYSE: ACME",
        **row,
    }


def _globex(**row):
    return {
        "ISSUERCIK": "0000002000",
        "ISSUERNAME": "Globex Inc.",
        "ISSUERTRADINGSYMBOL": "GLBX",
        **row,
    }


ROWS = {
    "SUBMISSION.tsv": [
        _acme(
            ACCESSION_NUMBER="0000005001-26-000001",
            FILING_DATE="05-AUG-2026",
            PERIOD_OF_REPORT="03-AUG-2026",
            DOCUMENT_TYPE="4",
            AFF10B5ONE="0",
        ),
        _globex(
            ACCESSION_NUMBER="0000005001-26-000002",
            FILING_DATE="12-AUG-2026",
            PERIOD_OF_REPORT="10-AUG-2026",
            DOCUMENT_TYPE="4",
            AFF10B5ONE="false",
        ),
        _globex(
            ACCESSION_NUMBER="0000005002-26-000003",
            FILING_DATE="20-AUG-2026",
            PERIOD_OF_REPORT="18-AUG-2026",
            DOCUMENT_TYPE="4",
            AFF10B5ONE="true",
            REMARKS='Sold under a "plan"',
        ),
        _globex(
            ACCESSION_NUMBER="0000005002-26-000004",
            FILING_DATE="25-AUG-2026",
            PERIOD_OF_REPORT="18-AUG-2026",
            DATE_OF_ORIG_SUB="20-AUG-2026",
            DOCUMENT_TYPE="4/A",
            AFF10B5ONE="1",
        ),
        _acme(
            ACCESSION_NUMBER="0000005003-26-000005",
            FILING_DATE="01-SEP-2026",
            PERIOD_OF_REPORT="28-AUG-2026",
            DOCUMENT_TYPE="4",
            AFF10B5ONE="0",
        ),
        _acme(
            ACCESSION_NUMBER="0000005005-26-000006",
            FILING_DATE="02-SEP-2026",
            PERIOD_OF_REPORT="31-AUG-2026",
            DOCUMENT_TYPE="3",
        ),
        _acme(
            ACCESSION_NUMBER="0000009999-26-000007", FILING_DATE="03-SEP-2026", DOCUMENT_TYPE="10-K"
        ),  # not an insider form: ignored
    ],
    "REPORTINGOWNER.tsv": [
        {
            "ACCESSION_NUMBER": "0000005001-26-000001",
            "RPTOWNERCIK": "0000005001",
            "RPTOWNERNAME": "DOE JANE A",
            "RPTOWNER_RELATIONSHIP": "Director",
            "RPTOWNER_STREET1": SECRET_ADDRESS[0],
            "RPTOWNER_CITY": SECRET_ADDRESS[1],
            "RPTOWNER_STATE": "CA",
            "RPTOWNER_ZIPCODE": SECRET_ADDRESS[2],
        },
        {
            "ACCESSION_NUMBER": "0000005001-26-000002",
            "RPTOWNERCIK": "0000005001",
            "RPTOWNERNAME": "DOE JANE A",
            "RPTOWNER_RELATIONSHIP": "Director",
            "RPTOWNER_STREET1": SECRET_ADDRESS[0],
            "RPTOWNER_ZIPCODE": SECRET_ADDRESS[2],
        },
        {
            "ACCESSION_NUMBER": "0000005002-26-000003",
            "RPTOWNERCIK": "0000005002",
            "RPTOWNERNAME": "ROE RICHARD",
            "RPTOWNER_RELATIONSHIP": "Officer",
            "RPTOWNER_TITLE": "Chief Financial Officer",
        },
        {
            "ACCESSION_NUMBER": "0000005002-26-000004",
            "RPTOWNERCIK": "0000005002",
            "RPTOWNERNAME": "ROE RICHARD",
            "RPTOWNER_RELATIONSHIP": "Officer",
            "RPTOWNER_TITLE": "Chief Financial Officer",
        },
        {
            "ACCESSION_NUMBER": "0000005003-26-000005",
            "RPTOWNERCIK": "0000005003",
            "RPTOWNERNAME": "Big Fund, L.P.",
            "RPTOWNER_RELATIONSHIP": "TenPercentOwner",
        },
        {
            "ACCESSION_NUMBER": "0000005003-26-000005",
            "RPTOWNERCIK": "0000005004",
            "RPTOWNERNAME": "Big Fund GP, LLC",
            "RPTOWNER_RELATIONSHIP": "TenPercentOwner",
        },
        {
            "ACCESSION_NUMBER": "0000005005-26-000006",
            "RPTOWNERCIK": "0000005005",
            "RPTOWNERNAME": "SMITH JOHN Q JR",
            "RPTOWNER_RELATIONSHIP": "Director,Officer",
            "RPTOWNER_TITLE": "Chief Executive Officer",
        },
        {
            "ACCESSION_NUMBER": "0000009999-26-000007",
            "RPTOWNERCIK": "0000009999",
            "RPTOWNERNAME": "IGNORED PERSON",
            "RPTOWNER_RELATIONSHIP": "Director",
        },
    ],
    "NONDERIV_TRANS.tsv": [
        {
            "ACCESSION_NUMBER": "0000005001-26-000001",
            "NONDERIV_TRANS_SK": "1",
            "SECURITY_TITLE": "Common Stock",
            "TRANS_DATE": "03-AUG-2026",
            "TRANS_CODE": "P",
            "TRANS_SHARES": "1000.0",
            "TRANS_PRICEPERSHARE": "10.0",
            "TRANS_ACQUIRED_DISP_CD": "A",
            "SHRS_OWND_FOLWNG_TRANS": "5000.0",
            "DIRECT_INDIRECT_OWNERSHIP": "D",
        },
        {
            "ACCESSION_NUMBER": "0000005001-26-000002",
            "NONDERIV_TRANS_SK": "2",
            "SECURITY_TITLE": "Common Stock",
            "TRANS_DATE": "10-AUG-2026",
            "TRANS_CODE": "A",
            "TRANS_SHARES": "300.0",
            "TRANS_PRICEPERSHARE": "0.0",
            "TRANS_ACQUIRED_DISP_CD": "A",
            "SHRS_OWND_FOLWNG_TRANS": "300.0",
            "DIRECT_INDIRECT_OWNERSHIP": "D",
        },
        {
            "ACCESSION_NUMBER": "0000005002-26-000003",
            "NONDERIV_TRANS_SK": "3",
            "SECURITY_TITLE": "Common Stock",
            "TRANS_DATE": "18-AUG-2026",
            "TRANS_CODE": "S",
            "TRANS_SHARES": "500.0",
            "TRANS_PRICEPERSHARE": "12.5",
            "TRANS_ACQUIRED_DISP_CD": "D",
            "SHRS_OWND_FOLWNG_TRANS": "9500.0",
            "DIRECT_INDIRECT_OWNERSHIP": "D",
        },
        {
            "ACCESSION_NUMBER": "0000005002-26-000003",
            "NONDERIV_TRANS_SK": "4",
            "SECURITY_TITLE": "Common Stock",
            "TRANS_DATE": "18-AUG-2026",
            "TRANS_CODE": "F",
            "TRANS_SHARES": "100.0",
            "TRANS_PRICEPERSHARE": "12.0",
            "TRANS_ACQUIRED_DISP_CD": "D",
            "SHRS_OWND_FOLWNG_TRANS": "9400.0",
            "DIRECT_INDIRECT_OWNERSHIP": "D",
        },
        {
            "ACCESSION_NUMBER": "0000005002-26-000004",
            "NONDERIV_TRANS_SK": "5",
            "SECURITY_TITLE": "Common Stock",
            "TRANS_DATE": "18-AUG-2026",
            "TRANS_CODE": "S",
            "TRANS_SHARES": "500.0",
            "TRANS_PRICEPERSHARE": "12.75",
            "TRANS_ACQUIRED_DISP_CD": "D",
            "SHRS_OWND_FOLWNG_TRANS": "9500.0",
            "DIRECT_INDIRECT_OWNERSHIP": "D",
        },
        {
            "ACCESSION_NUMBER": "0000005003-26-000005",
            "NONDERIV_TRANS_SK": "6",
            "SECURITY_TITLE": "Common Stock",
            "TRANS_DATE": "28-AUG-2026",
            "TRANS_CODE": "S",
            "TRANS_SHARES": "250000.0",
            "TRANS_PRICEPERSHARE": "9.5",
            "TRANS_ACQUIRED_DISP_CD": "D",
            "SHRS_OWND_FOLWNG_TRANS": "4750000.0",
            "DIRECT_INDIRECT_OWNERSHIP": "I",
        },
    ],
    "DERIV_TRANS.tsv": [
        {
            "ACCESSION_NUMBER": "0000005001-26-000002",
            "DERIV_TRANS_SK": "1",
            "SECURITY_TITLE": "Stock Option (right to buy)",
            "TRANS_DATE": "10-AUG-2026",
            "TRANS_CODE": "A",
            "TRANS_SHARES": "2000.0",
            "TRANS_PRICEPERSHARE": "0.0",
            "TRANS_ACQUIRED_DISP_CD": "A",
            "SHRS_OWND_FOLWNG_TRANS": "2000.0",
            "DIRECT_INDIRECT_OWNERSHIP": "D",
        },
    ],
}


def write_dataset(path: Path, rows: dict[str, list[dict]] = ROWS) -> Path:
    """Write tables as the SEC does: tab-separated, LF line endings, no quoting."""
    with zipfile.ZipFile(path, "w") as archive:
        for member, header in COLUMNS.items():
            columns = header.split()
            lines = ["\t".join(columns)]
            lines += [
                "\t".join(row.get(col, "") for col in columns) for row in rows.get(member, [])
            ]
            archive.writestr(member, "\n".join(lines) + "\n")
    return path


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in an empty folder, so a developer's own .env or data/ never leaks in."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    return write_dataset(tmp_path / "2026q3_form345.zip")


@pytest.fixture
def db_path(tmp_path: Path, dataset: Path) -> Path:
    path = tmp_path / "graph.db"
    conn = store.connect(path)
    try:
        load.load_quarter(conn, dataset)
    finally:
        conn.close()
    return path


@pytest.fixture
def db(db_path: Path):
    conn = store.connect(db_path)
    yield conn
    conn.close()
