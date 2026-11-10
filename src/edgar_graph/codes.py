"""What each Form 4 transaction code means, in plain words."""

# code: (short label, plain-language meaning, category used for colour in the site)
CODES: dict[str, tuple[str, str, str]] = {
    "P": ("Purchase", "Open-market or private purchase", "buy"),
    "S": ("Sale", "Open-market or private sale", "sell"),
    "A": ("Grant or award", "Grant, award or other acquisition from the company", "grant"),
    "D": ("Returned to company", "Sale or transfer back to the company", "other"),
    "F": (
        "Tax withholding",
        "Shares withheld or delivered to pay an exercise price or taxes",
        "tax",
    ),
    "I": ("Discretionary", "Discretionary transaction in an employee benefit plan", "other"),
    "M": (
        "Option or RSU exercise",
        "Exercise or conversion of an exempt derivative, such as stock options or RSUs",
        "exercise",
    ),
    "C": ("Conversion", "Conversion of a derivative security", "exercise"),
    "E": ("Short position expired", "Expiration of a short derivative position", "other"),
    "H": (
        "Long position expired",
        "Expiration or cancellation of a long derivative position, with value received",
        "other",
    ),
    "O": ("Exercise, out of the money", "Exercise of an out-of-the-money derivative", "exercise"),
    "X": (
        "Exercise, in the money",
        "Exercise of an in-the-money or at-the-money derivative",
        "exercise",
    ),
    "G": ("Gift", "Bona fide gift, given or received", "gift"),
    "L": ("Small acquisition", "Small acquisition exempt under Rule 16a-6", "other"),
    "W": ("Will or inheritance", "Acquired or disposed of by will or inheritance", "other"),
    "Z": ("Voting trust", "Deposit into or withdrawal from a voting trust", "other"),
    "J": (
        "Other",
        "Other acquisition or disposition, described in the filing's footnotes",
        "other",
    ),
    "K": ("Equity swap", "Transaction in an equity swap or similar instrument", "other"),
    "U": (
        "Tendered in a takeover",
        "Disposed of in a tender offer for a change of control",
        "other",
    ),
    "V": ("Voluntary early report", "Reported voluntarily, earlier than required", "other"),
}


def label(code: str | None) -> str:
    """Short label for a transaction code, or the raw code if the SEC adds new ones."""
    if code in CODES:
        return CODES[code][0]
    return code or "Not reported"


def as_json() -> dict[str, dict[str, str]]:
    return {
        code: {"label": short, "description": meaning, "category": category}
        for code, (short, meaning, category) in CODES.items()
    }
