from conftest import ACME, FUND, GLOBEX, JANE, JOHN, RICHARD, write_dataset

from edgar_graph import load, store


def by_cik(links):
    return {link.other.cik: link for link in links}


def test_company_links_list_its_insiders_with_roles(db):
    insiders = by_cik(store.links(db, ACME))
    assert set(insiders) == {JANE, FUND, 5004, JOHN}
    assert all(link.direction == "insider" for link in insiders.values())
    assert insiders[JANE].roles == ("Director",)
    assert insiders[FUND].roles == ("10% owner",)
    assert insiders[JOHN].roles == ("Director", "Officer")
    assert insiders[JOHN].title == "Chief Executive Officer"


def test_person_links_point_to_their_companies(db):
    companies = by_cik(store.links(db, JANE))
    assert set(companies) == {ACME, GLOBEX}
    assert all(link.direction == "insider_of" for link in companies.values())
    cfo = by_cik(store.links(db, RICHARD))[GLOBEX]
    assert (cfo.filings, cfo.first_filed, cfo.last_filed) == (2, "2026-08-20", "2026-08-25")


def test_shared_director_connects_two_companies(db):
    acme = store.get_entity(db, ACME)
    hood = store.neighborhood(db, acme, store.links(db, ACME))
    assert [e.cik for e in hood.ring2] == [GLOBEX]
    assert (JANE, GLOBEX) in hood.edges
    assert hood.left_out == 0


def test_top_directors_finds_the_interlock(db):
    [(person, companies)] = store.top_directors(db)
    assert person.cik == JANE
    assert [c.cik for c in companies] == [ACME, GLOBEX]


def test_largest_sales_skip_amendments_and_carry_joint_filers(db):
    sales = store.largest_trades(db, "S")
    assert [(t.accession, t.value) for t in sales] == [
        ("0000005003-26-000005", 2_375_000.0),
        ("0000005002-26-000003", 6_250.0),
    ]
    assert {owner.cik for owner in sales[0].owners} == {FUND, 5004}
    assert sales[0].url == (
        "https://www.sec.gov/Archives/edgar/data/1000/000000500326000005/"
        "0000005003-26-000005-index.htm"
    )


def widget_sale(n: int, owner: int, name: str, shares: int, price: float) -> dict[str, dict]:
    accession = f"0000006000-26-{n:06d}"
    return {
        "SUBMISSION.tsv": {
            "ACCESSION_NUMBER": accession,
            "FILING_DATE": "10-SEP-2026",
            "DOCUMENT_TYPE": "4",
            "ISSUERCIK": "0000003000",
            "ISSUERNAME": "WIDGET INC",
            "ISSUERTRADINGSYMBOL": "WDGT",
        },
        "REPORTINGOWNER.tsv": {
            "ACCESSION_NUMBER": accession,
            "RPTOWNERCIK": str(owner),
            "RPTOWNERNAME": name,
            "RPTOWNER_RELATIONSHIP": "Officer",
        },
        "NONDERIV_TRANS.tsv": {
            "ACCESSION_NUMBER": accession,
            "NONDERIV_TRANS_SK": str(n),
            "TRANS_DATE": "08-SEP-2026",
            "TRANS_CODE": "S",
            "TRANS_SHARES": str(shares),
            "TRANS_PRICEPERSHARE": str(price),
            "TRANS_ACQUIRED_DISP_CD": "D",
            "DIRECT_INDIRECT_OWNERSHIP": "D",
        },
    }


def test_largest_trades_drop_price_typos_and_repeat_filings(tmp_path):
    sales = [
        widget_sale(1, 6001, "ALPHA ANN", 100, 20.0),
        widget_sale(2, 6002, "BETA BOB", 200, 21.0),
        widget_sale(3, 6003, "GAMMA GIL", 300, 19.5),
        widget_sale(4, 6004, "DELTA DEB", 50, 20_000.0),  # a total typed as the price
        widget_sale(5, 6005, "EPSILON EVE", 10_000, 20.5),
        widget_sale(6, 6005, "EPSILON EVE", 10_000, 20.5),  # the same filing sent twice
        widget_sale(7, 6006, "EVE FAMILY TRUST", 10_000, 20.5),  # an affiliate reporting it too
    ]
    tables = {member: [sale[member] for sale in sales] for member in sales[0]}
    conn = store.connect(":memory:")
    load.load_quarter(conn, write_dataset(tmp_path / "2026q3_form345.zip", tables))
    check = store.PriceCheck(conn)

    biggest = store.largest_trades(conn, "S", 10, check)

    assert [trade.owners[0].cik for trade in biggest] == [6005, 6003, 6002, 6001]
    assert [owner.cik for owner in biggest[0].owners] == [6005, 6006]
    [typo], _ = store.transactions(conn, 6004)
    assert check.status(typo) == "unusual"
    assert check.status(biggest[0]) == "ok"
    assert len(store.largest_trades(conn, "S", 10)) == 5  # unchecked: the typo leads


def test_prices_with_too_few_comparisons_are_unverified(db):
    check = store.PriceCheck(db)
    fund_sale = store.largest_trades(db, "S")[0]
    assert check.status(fund_sale) == "unverified"


def test_transactions_for_a_person_include_amendments(db):
    trades, total = store.transactions(db, RICHARD)
    assert total == 3
    assert sum(trade.amended for trade in trades) == 1
    assert {trade.issuer.cik for trade in trades} == {GLOBEX}


def test_resolve_by_cik_ticker_and_name(db):
    assert store.resolve(db, "1000").cik == ACME
    assert store.resolve(db, "acme").cik == ACME
    assert store.resolve(db, "Jane A Doe").cik == JANE
    assert store.resolve(db, "DOE JANE A").cik == JANE
    assert store.resolve(db, "nobody") is None


def test_search_matches_every_word_busiest_first(db):
    assert [e.cik for e, _ in store.search(db, "doe")] == [JANE]
    assert [e.cik for e, _ in store.search(db, "jane doe")] == [JANE]
    assert store.search(db, "fund")[0][0].kind == "organization"
    assert store.search(db, "   ") == []
