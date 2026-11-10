import json

from conftest import ACME, FUND, GLOBEX, JANE, SECRET_ADDRESS

from edgar_graph import export


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_export_writes_app_and_data(db, tmp_path):
    out = tmp_path / "public"
    result = export.export_site(db, out)

    assert result.entities == 7
    for name in ("index.html", "app.js", "style.css"):
        assert (out / name).is_file()
    meta = read(out / "data" / "meta.json")
    assert meta["quarters"] == ["2026q3"] and meta["filings"] == 6
    assert read(out / "data" / "codes.json")["P"]["label"] == "Purchase"

    index = read(out / "data" / "search.json")
    assert len(index) == 7
    assert {row[0] for row in index} >= {ACME, GLOBEX, JANE}

    feed = read(out / "data" / "feed.json")
    # Too few other insiders reported prices at these companies to vouch for any trade.
    assert feed["purchases"] == [] and feed["sales"] == []
    assert feed["directors"][0]["person"][0] == JANE


def test_entity_pages_carry_links_trades_sources_and_map(db, tmp_path):
    out = tmp_path / "public"
    export.export_site(db, out)
    acme = read(out / "data" / "e" / f"{ACME}.json")

    assert acme["display"] == "ACME CORP" and acme["ticker"] == "ACME"
    assert {link["ent"][0] for link in acme["links"]} == {JANE, FUND, 5004, 5005}
    assert all(t["url"].startswith("https://www.sec.gov/Archives/edgar/data/") for t in acme["tx"])
    rings = {node["id"]: node["ring"] for node in acme["graph"]["nodes"]}
    assert rings[ACME] == 0 and rings[JANE] == 1 and rings[GLOBEX] == 2


def test_exported_site_contains_no_street_addresses(db, tmp_path):
    out = tmp_path / "public"
    export.export_site(db, out)
    text = "".join(p.read_text(encoding="utf-8") for p in out.rglob("*.json"))
    for secret in SECRET_ADDRESS:
        assert secret not in text
