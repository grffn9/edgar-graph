"""Reader-friendly names and tickers.

These are display heuristics only. The as-filed name is always kept and shown alongside,
and identity is always the SEC's CIK, never a name.
"""

import re

# Words that mark a reporting owner as an organization (fund, trust, company) rather than a person.
_ORG_WORDS = frozenset(
    [
        "LLC",
        "LP",
        "LLP",
        "LLLP",
        "INC",
        "INCORPORATED",
        "CORP",
        "CORPORATION",
        "COMPANY",
        "LTD",
        "LIMITED",
        "PLC",
        "TRUST",
        "TRUSTS",
        "FUND",
        "FUNDS",
        "PARTNERS",
        "PARTNERSHIP",
        "CAPITAL",
        "HOLDINGS",
        "HOLDING",
        "GROUP",
        "MANAGEMENT",
        "ADVISORS",
        "ADVISERS",
        "ADVISORY",
        "INVESTMENT",
        "INVESTMENTS",
        "INVESTORS",
        "VENTURE",
        "VENTURES",
        "FOUNDATION",
        "BANK",
        "BANCORP",
        "ASSOCIATES",
        "SECURITIES",
        "FINANCIAL",
        "INSURANCE",
        "ENTERPRISES",
        "SPONSOR",
        "ESTATE",
        "UNIVERSITY",
        "ENDOWMENT",
        "PENSION",
        "CHARITABLE",
        "SOCIETY",
        "SA",
        "AG",
        "NV",
        "BV",
        "GMBH",
        "SE",
        "SPA",
        "SARL",
        "PTE",
        "PTY",
        "KG",
        "GP",
        "SICAV",
        "AB",
        "ASA",
        "OY",
    ]
)
_SUFFIXES = {
    "JR": "Jr",
    "SR": "Sr",
    "II": "II",
    "III": "III",
    "IV": "IV",
    "MD": "MD",
    "PHD": "PhD",
    "ESQ": "Esq",
    "CPA": "CPA",
}
_PARTICLES = frozenset(
    [
        "DE",
        "DEL",
        "DELLA",
        "DA",
        "DI",
        "DOS",
        "DU",
        "LA",
        "LE",
        "VAN",
        "VON",
        "DER",
        "DEN",
        "TER",
        "ST",
        "MAC",
    ]
)
# EDGAR appends state or status tags to some names: "ACME CORP /DE/", "BROOKFIELD Corp /ON/".
_EDGAR_TAG = re.compile(r"\s+/[A-Z]{2,4}/?\s*$")
_EXCHANGE_PREFIX = re.compile(
    r"^(?:NYSE AMERICAN|NYSE ARCA|NYSE|NASDAQ|AMEX|OTCQX|OTCQB|OTC|CBOE|ASX|TSXV|TSX)\s*[:/]\s*"
)
_NO_TICKER = frozenset({"", "N/A", "NA", "NONE", "NULL", "-"})


def _bare(token: str) -> str:
    return token.upper().replace(".", "").strip(",()")


def is_organization(name: str) -> bool:
    """Guess whether a reporting owner is an organization rather than a person."""
    if "&" in name or any(ch.isdigit() for ch in name):
        return True
    return any(_bare(token) in _ORG_WORDS for token in name.split())


def display_name(name: str, kind: str) -> str:
    """'COOK TIMOTHY D' -> 'Timothy D Cook'. Companies and organizations keep their filed case."""
    base = _EDGAR_TAG.sub("", name.strip()) or name.strip()
    return _person_name(base) if kind == "person" else base


def _person_name(name: str) -> str:
    if "," in name:  # "Smith, John A."
        surname_part, _, given_part = name.partition(",")
        surname, given = surname_part.split(), given_part.replace(",", " ").split()
    else:  # filings list people surname first: "VAN DER BERG JOHN"
        tokens = name.split()
        if len(tokens) < 2:
            return _cased(tokens, name)
        i = 0
        while i < len(tokens) - 2 and _bare(tokens[i]) in _PARTICLES:
            i += 1
        surname, given = tokens[: i + 1], tokens[i + 1 :]
    suffix: list[str] = []
    while len(given) > 1 and _bare(given[-1]) in _SUFFIXES:
        suffix.insert(0, given.pop())
    return _cased(given + surname + suffix, name)


def _cased(tokens: list[str], original: str) -> str:
    if any(ch.islower() for ch in original):  # the filer chose the case; keep it
        return " ".join(tokens)
    return " ".join(_case_word(token) for token in tokens)


def _case_word(word: str) -> str:
    bare = _bare(word)
    if bare in _SUFFIXES:
        return _SUFFIXES[bare] + ("." if word.endswith(".") else "")
    if len(bare) == 1 or (len(bare) <= 3 and "." in word):  # initials: D, R., K.M.
        return word
    parts = re.split(r"([-'’])", word.lower())
    cased = "".join(part[:1].upper() + part[1:] for part in parts)
    return re.sub(r"^Mc([a-z])", lambda m: "Mc" + m.group(1).upper(), cased)


def clean_ticker(raw: str) -> str | None:
    """Tidy the free-text symbol filers type: 'NYSE: VTEX' -> 'VTEX', '[NONE]' -> None."""
    ticker = _EXCHANGE_PREFIX.sub("", raw.strip().upper()).strip("()[] ")
    return None if ticker in _NO_TICKER else ticker


def ticker_symbols(ticker: str | None) -> set[str]:
    """Every symbol in a cleaned ticker field: 'LEN, LEN.B' -> {'LEN', 'LEN.B'}."""
    if not ticker:
        return set()
    return {part for part in re.split(r"[\s,/]+|\bAND\b", ticker.upper()) if part}
