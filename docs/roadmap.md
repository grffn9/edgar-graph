# Roadmap

The goal: link SEC filings into a knowledge graph of people, companies and funds, where every fact cites its source filing, and publish it as a free, easy-to-explore public site.

## What EDGAR can tell us

| Source | What it reveals | Format | Phase |
|---|---|---|---|
| Forms 3/4/5 | Every director, officer and 10% owner, and each trade, grant, gift and option exercise (10b5-1 plan flag since 2023) | Quarterly data sets since 2006, about 10 MB each | 1 (done) |
| Daily index + filing XML | Same-day insider filings between quarterly releases | Index files, XML | 2 |
| Schedules 13D/13G | Who owns 5%+, and activists' stated plans | XML since Dec 2024, text before | 2 |
| Form 13F | Holdings of every manager with $100M+ | Data sets keyed by CUSIP (a licensed ID: don't republish in bulk) | 2 |
| Form 144 | Insiders' notices of planned sales | XML since 2023 | 2 |
| Form D | Private fundraises and the people behind them | Data sets | 3 |
| 8-K items | Officer departures and appointments (5.02), auditor changes (4.01), restatements (4.02), deals (2.01), bankruptcy (1.03) | Item codes in metadata; text parsing | 3 |
| 10-K Exhibit 21 | Subsidiaries (corporate family trees) | HTML/text parsing | 3 |
| Submissions + XBRL APIs | Former names, tickers, industry, financials, auditor, CEO pay vs performance | JSON plus nightly bulk zips | 4 |

Public figures: EDGAR covers anyone who is a corporate insider, large holder or fund manager. Members of Congress' own trades are not in EDGAR; they're in House and Senate STOCK Act reports. Wikidata records SEC CIKs (property P5531) next to other IDs, which makes it the natural bridge between datasets.

## Graph model

- Nodes: company, person, organization (funds, trusts, holding companies) and filing. Later: security, offering, event, audit firm.
- Edges: insider-of (role, title, first and last filing) and reported transaction. Later: owns-5%, holds (13F), subsidiary-of, related-person-of (Form D), audited-by. Derived: board interlock (two companies share a director).
- Rules: the CIK is identity and people are never merged by name. Every edge keeps its filing's accession number and dates, so any fact links to sec.gov and the graph can be viewed as of a date.

## Phases

1. **Insiders (done).** Whole market from the quarterly data sets, SQLite graph, CLI, static site with entity pages, connections maps, largest trades and board interlocks.
2. **Fresher and fuller ownership.** Daily top-up from the EDGAR daily index (same parser fed by per-filing XML); fold amendments into the filings they correct; Schedules 13D/13G; Form 13F with an open identifier mapping (FIGI) instead of republished CUSIPs; Form 144.
3. **Events and structure.** Form D raises and related persons; 8-K items next to insider activity; Exhibit 21 subsidiaries.
4. **Context.** Former names, industries and financials from the submissions and XBRL APIs; auditor and pay-versus-performance data; enrichment from Wikidata and GLEIF (legal entity IDs and parent companies).
5. **Public launch.** Hosting (GitHub Pages or a bucket, sharding entity files past host file limits), open-data downloads (SQLite, CSV), per-entity RSS, an as-of-date view, Datasette for journalists, a corrections process, and code and data licenses. Optional: link House and Senate STOCK Act reports through Wikidata.

## Guardrails

- Facts as filed, in neutral words; no "suspicious" labels.
- No street addresses of individuals stored or published.
- Identity only by CIK; display names are a heuristic shown next to the filed name.
- Prices checked against other insiders' prices before a trade can headline a list.
- SEC fair access: a declared User-Agent, at most 10 requests per second, cached downloads, bulk files for volume.
- Not investment advice. Filing text is untrusted input and is rendered as text only.
