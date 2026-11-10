# edgar-graph

Turns SEC insider filings into a public knowledge graph: which people and funds are insiders at which US public companies, what they reported buying, selling, being granted or giving away, and which companies are tied together through shared directors. It builds one SQLite file from the SEC's bulk data and exports a static website anyone can browse, with every transaction linked to its filing on sec.gov.

Version 1 covers Forms 3, 4 and 5 (every director, officer and 10% owner), loaded from the SEC's quarterly [insider transactions data sets](https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets). [docs/roadmap.md](docs/roadmap.md) covers what comes next: ownership filings, private fundraising, corporate events and links to public officials.

## Install

Needs [uv](https://docs.astral.sh/uv/), which installs Python 3.12 if you don't have it.

```bash
git clone <repo-url> edgar-graph
cd edgar-graph
uv sync
```

The SEC asks every automated client to identify itself with a name and contact email. Copy `.env.example` to `.env` and fill it in (`.env` is gitignored), or set the variable in your shell:

```bash
EDGAR_USER_AGENT="Jane Doe jane@example.com"
```

## Use

Load a quarter. Each one is a single download of about 10 MB, and the data sets go back to 2006q1:

```console
$ uv run edgar-graph ingest 2026q3
Downloading 2026q3 from sec.gov ...
Loaded 2026q3: 38,152 filings, 41,346 insider reports, 79,090 transactions
Graph: 4,716 companies, 22,122 people, 1,598 funds and other organizations, 26,133 insider links (data/edgar-graph.db)
```

Look up a company, person or fund by ticker, CIK or name:

```console
$ uv run edgar-graph show AAPL
Apple Inc. (AAPL)
company, SEC CIK 320193, https://www.sec.gov/edgar/browse/?CIK=320193

Insiders (6)
  Jennifer Newstead                   Officer                   SVP, GC and Government Af...  2026-08-13 to 2026-09-29
  John Ternus                         Director, Officer         CEO                           2026-09-01 to 2026-09-29
  ...
  Timothy D Cook                      Director, Officer         Executive Chair               2026-09-29 to 2026-09-29

Connected companies (shared insiders) (2)
  NIKE, Inc. (NKE)                          via Timothy D Cook
  EMERSON ELECTRIC CO (EMR)                 via Jennifer Newstead

Transactions (10 most recent of 23)
  2026-09-27  Timothy D Cook                  A Grant or award   +65,969   0001140361-26-038021  derivative: Restricted Stock Unit
  ...
```

```bash
uv run edgar-graph search "cook"    # find by any part of a name or ticker
uv run edgar-graph stats            # what's loaded
```

Build the public site and preview it:

```console
$ uv run edgar-graph export public
Wrote 28,443 files (123.4 MB) to public
$ uv run edgar-graph serve public
Serving public at http://127.0.0.1:8000/  (Ctrl+C to stop)
```

The site has search, a page for every company, person and fund (a connections map, roles, and every transaction with its sec.gov link), a home page with the largest purchases and sales and the directors on the most boards, and an About page that explains the method and every transaction code.

## Publish

`public/` is plain static files, so any static host works: GitHub Pages, Netlify or a storage bucket. One quarter is about 28,000 files and 120 MB. Some free hosts cap the number of files (Cloudflare Pages allows 20,000), so check before loading many quarters.

## How it works

| Module | Job |
|---|---|
| `sec.py` | Checks the User-Agent, spaces requests (under 7 a second; the SEC allows 10), finds and downloads a quarter's zip |
| `load.py` | Reads the zip's tab-separated tables into SQLite in one transaction; reloading a quarter replaces it |
| `store.py` | Schema and graph queries: links, transactions, two-hop neighborhoods, board interlocks, largest trades, price check |
| `names.py` | Readable names ("COOK TIMOTHY D" becomes "Timothy D Cook"), person or organization guess, ticker cleanup |
| `export.py` | Writes the site: the app files plus one JSON file per entity |
| `site/` | `index.html`, `app.js`, `style.css`, with no frameworks and no CDNs |

The graph: entities (company, person, organization) keyed by the SEC's CIK, filings, per-filing roles (director, officer, 10% owner, other), transactions, and a `link` table that rolls roles up per insider and company with first and last filing dates. `data/edgar-graph.db` is ordinary SQLite, so any SQLite tool can query it.

## Accuracy and privacy

- Facts are shown as filed, in neutral words. Transaction codes are explained, the 10b5-1 plan flag is shown, and amendments are labeled.
- Identity is the SEC's CIK only; people are never merged by name. Readable names are a heuristic, and the filed name is always shown too.
- Prices are typed by hand, so the largest-trade lists only include trades priced within 3× of the median price that at least three other insiders of the same company reported. Trades outside that range are marked "unusual price". One block reported in several filings is listed once, with every filer.
- Street addresses in the filings are never read, stored or published.
- Nothing here is investment advice.

## Develop

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy
```

The tests use a small hand-written data set in the SEC's real file layout (`tests/conftest.py`) and never touch the network.
