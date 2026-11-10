import os

from edgar_graph.cli import main


def run(capsys, *argv):
    code = main([str(arg) for arg in argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_ingest_a_local_file_then_show_and_search(capsys, tmp_path, dataset):
    db = tmp_path / "graph.db"
    code, out, _ = run(capsys, "--db", db, "ingest", "--file", dataset)
    assert code == 0
    assert "Loaded 2026q3: 6 filings, 7 insider reports, 7 transactions" in out

    code, out, _ = run(capsys, "--db", db, "show", "ACME")
    assert code == 0
    assert "Jane A Doe" in out and "Director" in out
    assert "Connected companies (shared insiders)" in out and "Globex Inc. (GLBX)" in out

    code, out, _ = run(capsys, "--db", db, "show", "Jane A Doe")
    assert code == 0
    assert "Insider of (2)" in out and "Purchase" in out

    code, out, _ = run(capsys, "--db", db, "search", "doe")
    assert code == 0 and "Jane A Doe" in out

    code, out, _ = run(capsys, "--db", db, "stats")
    assert code == 0 and "Companies:     2" in out


def test_export_writes_a_site(capsys, tmp_path, db_path):
    out_dir = tmp_path / "site"
    code, out, _ = run(capsys, "--db", db_path, "export", out_dir)
    assert code == 0
    assert (out_dir / "index.html").is_file() and "Wrote" in out


def test_unknown_entity_is_a_friendly_error(capsys, db_path):
    code, _, err = run(capsys, "--db", db_path, "show", "NOPE")
    assert code == 1
    assert "No company, person or fund matches 'NOPE'" in err


def test_missing_database_is_a_friendly_error(capsys, tmp_path):
    code, _, err = run(capsys, "--db", tmp_path / "missing.db", "stats")
    assert code == 1
    assert "No database at" in err and "ingest" in err


def test_bad_quarter_is_rejected_before_any_download(capsys, tmp_path):
    code, _, err = run(capsys, "--db", tmp_path / "g.db", "ingest", "2026q9")
    assert code == 1
    assert "not a quarter like 2026q3" in err


def test_settings_can_come_from_a_dotenv_file(capsys, tmp_path, monkeypatch, dataset):
    monkeypatch.delenv("EDGAR_GRAPH_DB", raising=False)
    (tmp_path / ".env").write_text("# local\nEDGAR_GRAPH_DB=from-dotenv.db\n", encoding="utf-8")
    code, _, _ = run(capsys, "ingest", "--file", dataset)
    assert code == 0
    assert (tmp_path / "from-dotenv.db").is_file()


def test_shell_variables_win_over_dotenv(tmp_path, monkeypatch):
    from edgar_graph.cli import load_env_file

    monkeypatch.setenv("EDGAR_USER_AGENT", "Shell Person shell@example.com")
    env = tmp_path / ".env"
    env.write_text('EDGAR_USER_AGENT="File Person file@example.com"\n', encoding="utf-8")
    load_env_file(env)
    assert os.environ["EDGAR_USER_AGENT"] == "Shell Person shell@example.com"


def test_download_needs_a_contact_user_agent(capsys, tmp_path, monkeypatch):
    monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)
    code, _, err = run(
        capsys, "--db", tmp_path / "g.db", "ingest", "2026q3", "--data-dir", tmp_path / "raw"
    )
    assert code == 1
    assert "EDGAR_USER_AGENT" in err
    assert not (tmp_path / "raw").exists()
