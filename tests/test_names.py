import pytest

from edgar_graph.names import clean_ticker, display_name, is_organization, ticker_symbols


@pytest.mark.parametrize(
    ("filed", "expected"),
    [
        ("COOK TIMOTHY D", "Timothy D Cook"),
        ("AULT MILTON C III", "Milton C Ault III"),
        ("SMITH JOHN Q JR", "John Q Smith Jr"),
        ("VAN DER BERG JOHN", "John Van Der Berg"),
        ("LE JOHN", "John Le"),
        ("MCNAB PAUL", "Paul McNab"),
        ("O'BRIEN MARY-KATE", "Mary-Kate O'Brien"),
        ("BEALL PAMELA K.M.", "Pamela K.M. Beall"),
        ("McNab Paul", "Paul McNab"),  # mixed case is the filer's choice: only reorder
        ("Smith, John A.", "John A. Smith"),
        ("MADONNA", "Madonna"),
    ],
)
def test_person_display_names(filed, expected):
    assert display_name(filed, "person") == expected


def test_companies_keep_their_filed_name_minus_edgar_tags():
    assert display_name("BROOKFIELD Corp /ON/", "company") == "BROOKFIELD Corp"
    assert display_name("Apple Inc.", "company") == "Apple Inc."


@pytest.mark.parametrize(
    ("name", "org"),
    [
        ("Big Fund, L.P.", True),
        ("Silver Lake Partners V DE (AIV), L.P.", True),
        ("HRT FINANCIAL LP", True),
        ("667, L.P.", True),
        ("SMITH & WESSON", True),
        ("COOK TIMOTHY D", False),
        ("Cheng Chi Fung", False),
    ],
)
def test_is_organization(name, org):
    assert is_organization(name) is org


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AAPL", "AAPL"),
        ("NYSE: VTEX", "VTEX"),
        ("NASDAQ:RMR", "RMR"),
        ("(SIRI)", "SIRI"),
        ("[NONE]", None),
        ("N/A", None),
        ("", None),
        ("brk.a", "BRK.A"),
    ],
)
def test_clean_ticker(raw, expected):
    assert clean_ticker(raw) == expected


def test_ticker_symbols_splits_multi_class_tickers():
    assert ticker_symbols("LEN, LEN.B") == {"LEN", "LEN.B"}
    assert ticker_symbols("Z AND ZG") == {"Z", "ZG"}
    assert ticker_symbols(None) == set()
