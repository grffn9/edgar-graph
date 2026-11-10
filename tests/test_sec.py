import urllib.error

import pytest

from edgar_graph import sec

PAGE = sec.DATASET_PAGE
NEW = sec.KNOWN_FOLDERS[0] + "2026q3_form345.zip"
OLD = sec.KNOWN_FOLDERS[1] + "2026q3_form345.zip"
ZIP = b"PK\x03\x04 pretend zip"


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.headers: dict[str, str] = {}

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def fake_sec(responses: dict[str, bytes | int]):
    """An opener that answers from a dict: bytes are bodies, ints are HTTP errors (default 404)."""
    seen: list = []

    def opener(request, timeout):
        seen.append(request)
        answer = responses.get(request.full_url, 404)
        if isinstance(answer, int):
            raise urllib.error.HTTPError(request.full_url, answer, "error", {}, None)
        return FakeResponse(answer)

    return sec.SecClient("Jane Doe jane@example.com", min_interval=0, opener=opener), seen


def test_download_follows_the_link_on_the_data_set_page(tmp_path):
    page = b'<a href="/files/somewhere-new/2026q3_form345.zip">2026 Q3</a>'
    link = "https://www.sec.gov/files/somewhere-new/2026q3_form345.zip"
    client, seen = fake_sec({PAGE: page, link: ZIP})

    path = sec.download_quarter(client, "2026q3", tmp_path)

    assert path.read_bytes() == ZIP
    assert [r.full_url for r in seen] == [PAGE, link]
    assert seen[0].get_header("User-agent") == "Jane Doe jane@example.com"
    assert sec.download_quarter(client, "2026q3", tmp_path) == path  # cached: no new requests
    assert len(seen) == 2


def test_download_falls_back_to_known_folders(tmp_path):
    client, seen = fake_sec({PAGE: b"<html>no links</html>", OLD: ZIP})
    assert sec.download_quarter(client, "2026q3", tmp_path).read_bytes() == ZIP
    assert [r.full_url for r in seen] == [PAGE, NEW, OLD]


def test_unpublished_quarter_says_so(tmp_path):
    client, _ = fake_sec({PAGE: b"<html></html>"})
    with pytest.raises(sec.SecError, match="hasn't published 2026q3"):
        sec.download_quarter(client, "2026q3", tmp_path)
    assert not list(tmp_path.iterdir())


def test_refusal_points_at_the_user_agent(tmp_path):
    client, _ = fake_sec({PAGE: 403})
    with pytest.raises(sec.SecError, match="HTTP 403.*EDGAR_USER_AGENT"):
        sec.download_quarter(client, "2026q3", tmp_path)


def test_html_instead_of_zip_is_rejected(tmp_path):
    client, _ = fake_sec({PAGE: b"", NEW: b"<html>blocked</html>"})
    with pytest.raises(sec.SecError, match="other than a zip"):
        sec.download_quarter(client, "2026q3", tmp_path)


def test_user_agent_needs_a_contact_email(monkeypatch):
    monkeypatch.delenv(sec.USER_AGENT_ENV, raising=False)
    with pytest.raises(sec.SecError, match="Set EDGAR_USER_AGENT"):
        sec.user_agent()
    with pytest.raises(sec.SecError, match="needs a contact email"):
        sec.user_agent("Jane Doe")
    monkeypatch.setenv(sec.USER_AGENT_ENV, "Jane Doe jane@example.com")
    assert sec.user_agent() == "Jane Doe jane@example.com"


@pytest.mark.parametrize("good", ["2026q3", "2026Q3", " 2006q1 "])
def test_parse_quarter_accepts(good):
    assert sec.parse_quarter(good) == good.strip().lower()


@pytest.mark.parametrize("bad", ["2026q5", "2026-q3", "q3", "2005q4", ""])
def test_parse_quarter_rejects(bad):
    with pytest.raises(ValueError, match="not a quarter"):
        sec.parse_quarter(bad)
