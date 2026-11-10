"use strict";

// EDGAR Graph: a static site that reads the JSON written by `edgar-graph export`.
// Filing text comes from filers, so it is only ever inserted as text, never as HTML.
(() => {
  const app = document.getElementById("app");
  const input = document.getElementById("q");
  const results = document.getElementById("results");
  const SVG_NS = "http://www.w3.org/2000/svg";
  const KIND = { company: "Company", person: "Person", organization: "Fund or organization" };
  const cache = new Map();
  let codes = {};
  let index = null;
  let shown = [];
  let active = -1;

  const fmtCount = new Intl.NumberFormat("en-US");
  const fmtShares = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
  const fmtPrice = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 4,
  });
  const fmtMoney = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 1,
  });

  // ---------- helpers ----------

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child == null || child === false || child === "") continue;
      node.append(child instanceof Node ? child : String(child));
    }
    return node;
  }

  function svgEl(tag, attrs, text) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    if (text != null) node.textContent = text;
    return node;
  }

  function getJSON(path) {
    if (!cache.has(path)) {
      const request = fetch(path).then((response) => {
        if (!response.ok) throw new Error(`${response.status} ${path}`);
        return response.json();
      });
      request.catch(() => cache.delete(path));
      cache.set(path, request);
    }
    return cache.get(path);
  }

  const plural = (n, one, many = `${one}s`) => `${fmtCount.format(n)} ${n === 1 ? one : many}`;
  const shares = (v) => (v == null ? "" : fmtShares.format(v));
  const price = (v) => (v ? fmtPrice.format(v) : "");
  const money = (v) => (v ? fmtMoney.format(v) : "");
  const cut = (text, n) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);
  const href = (cik) => `#/e/${cik}`;
  const joined = (nodes, sep = ", ") => nodes.flatMap((node, i) => (i ? [sep, node] : [node]));
  const listWords = (words) =>
    words.length < 2 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words.at(-1)}`;
  const external = (url, text, title) =>
    el("a", { href: url, target: "_blank", rel: "noopener", title }, text);

  function entityLink([cik, display, , ticker]) {
    return el("a", { href: href(cik) }, display, ticker ? el("span", { class: "tick" }, ticker) : null);
  }

  function section(title, blurb, ...body) {
    return el(
      "section",
      { class: "block" },
      el("h2", {}, title),
      blurb ? el("p", { class: "muted" }, blurb) : null,
      body,
    );
  }

  function setView(...nodes) {
    app.replaceChildren(...nodes.filter(Boolean));
    app.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }

  // ---------- transactions ----------

  function codeCell(t) {
    const info = codes[t.code];
    return el(
      "span",
      { class: `code code-${info ? info.category : "other"}`, title: info ? info.description : null },
      el("b", {}, t.code || "?"),
      " ",
      info ? info.label : "Not reported",
      t.deriv && t.security ? el("span", { class: "security" }, t.security) : null,
    );
  }

  function ownersCell(owners) {
    if (!owners.length) return "";
    const more = owners.length - 1;
    return el(
      "span",
      {},
      entityLink(owners[0]),
      more ? el("span", { class: "muted small" }, ` +${more} joint filer${more > 1 ? "s" : ""}`) : null,
    );
  }

  function notes(t) {
    const tags = [];
    const tag = (text, title) => tags.push(el("span", { class: "tag", title }, text));
    if (t.plan === 1) {
      tag("10b5-1 plan", "The filer ticked the box saying this was made under a pre-arranged Rule 10b5-1 trading plan.");
    }
    if (t.form.endsWith("/A")) tag("amended", "From an amended filing that corrects an earlier one.");
    if (t.deriv) tag("derivative", `A derivative security: ${t.security || "an option, warrant or similar"}.`);
    if (t.own === "I") tag("indirect", "Held indirectly, for example through a trust, family member or fund.");
    if (t.odd) {
      tag(
        "unusual price",
        "This price is far from what other insiders of this company reported, so it may be a typing error in the filing. Check the source filing.",
      );
    }
    return tags;
  }

  function signedShares(t) {
    if (t.shares == null) return "";
    const sign = t.ad === "D" ? "−" : t.ad === "A" ? "+" : "";
    return el("span", { class: t.ad === "D" ? "neg" : "pos" }, `${sign}${shares(t.shares)}`);
  }

  const TRADE_COLUMNS = {
    date: ["Date", (t) => t.date || "—"],
    owner: ["Insider", (t) => ownersCell(t.owners)],
    issuer: ["Company", (t) => entityLink(t.issuer)],
    code: ["Transaction", codeCell],
    shares: ["Shares", signedShares, "num"],
    price: ["Price", (t) => price(t.price), "num"],
    value: ["Value", (t) => money(t.value), "num"],
    after: ["Owned after", (t) => shares(t.after), "num"],
    notes: ["Notes", notes],
    source: ["Source", (t) => external(t.url, `Form ${t.form}`, `SEC filing ${t.acc}`), "src"],
  };

  function tradeTable(trades, columns) {
    const cols = columns.map((key) => TRADE_COLUMNS[key]);
    return el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        {},
        el("thead", {}, el("tr", {}, cols.map(([title, , cls]) => el("th", { scope: "col", class: cls }, title)))),
        el(
          "tbody",
          {},
          trades.map((t) => el("tr", {}, cols.map(([, render, cls]) => el("td", { class: cls }, render(t))))),
        ),
      ),
    );
  }

  // ---------- home ----------

  function feedTable(trades, cols) {
    if (trades.length) return tradeTable(trades, cols);
    return el("p", { class: "muted" }, "No trades in the loaded filings passed the price check.");
  }

  function directorsList(rows) {
    if (!rows.length) {
      return el("p", { class: "muted" }, "No one in the loaded filings reported as a director at more than one company.");
    }
    return el(
      "ol",
      { class: "directors" },
      rows.map(({ person, companies }) =>
        el(
          "li",
          {},
          entityLink(person),
          el("span", { class: "muted" }, ` — ${companies.length} boards: `),
          joined(companies.map(entityLink)),
        ),
      ),
    );
  }

  async function renderHome() {
    const [meta, feed] = await Promise.all([getJSON("data/meta.json"), getJSON("data/feed.json")]);
    document.title = "EDGAR Graph";
    const stat = (n, label) => el("li", {}, el("strong", {}, fmtCount.format(n)), ` ${label}`);
    const cols = ["date", "owner", "issuer", "shares", "price", "value", "notes", "source"];
    setView(
      el(
        "section",
        { class: "hero" },
        el("h1", {}, "Who runs public companies, and what they do with their own stock"),
        el(
          "p",
          { class: "lede" },
          "Every director, officer and 10% owner of a US public company must report their trades in its stock " +
            `to the SEC. This site links ${fmtCount.format(meta.filings)} of those filings (Forms 3, 4 and 5, ` +
            `filed ${meta.first_filed} to ${meta.last_filed}) into one map of companies and the people and ` +
            "funds behind them. Every row links to the original filing on sec.gov.",
        ),
        el(
          "ul",
          { class: "stats" },
          stat(meta.companies, "companies"),
          stat(meta.people, "people"),
          stat(meta.organizations, "funds and other organizations"),
          stat(meta.transactions, "transactions"),
        ),
      ),
      section(
        "Largest purchases",
        "Insiders buying their own company's stock on the market or in a private deal (code P), by dollar value. " +
          "Only trades whose price matches what other insiders of the company reported are listed (see About).",
        feedTable(feed.purchases, cols),
      ),
      section(
        "Largest sales",
        "Insider sales by dollar value (code S), checked the same way. Sales made under a pre-arranged 10b5-1 plan are marked.",
        feedTable(feed.sales, cols),
      ),
      section(
        "Directors on the most boards",
        "People who reported as a director at more than one company. Shared directors are what tie companies together " +
          "in the graph. Fund families often share one board across many funds, so their directors lead this list.",
        directorsList(feed.directors),
      ),
    );
  }

  // ---------- entity ----------

  function summary(e, insiders, insiderOf) {
    const parts = [];
    if (insiders.length) {
      const roles = [
        ["Director", "director"],
        ["Officer", "officer"],
        ["10% owner", "10% owner"],
        ["Other insider", "other insider"],
      ]
        .map(([role, word]) => [insiders.filter((l) => l.roles.includes(role)).length, word])
        .filter(([count]) => count);
      const overlap = roles.reduce((sum, [count]) => sum + count, 0) > insiders.length;
      parts.push(
        `${plural(insiders.length, "insider")} reported on this company in the loaded filings ` +
          `(${roles.map(([count, word]) => plural(count, word)).join(", ")}` +
          `${overlap ? "; some hold more than one role" : ""}).`,
      );
    }
    if (insiderOf.length) {
      const roles = [...new Set(insiderOf.flatMap((l) => l.roles))].map((r) => r.toLowerCase());
      parts.push(`Reported as ${listWords(roles) || "an insider"} at ${plural(insiderOf.length, "company", "companies")}.`);
    }
    parts.push(`${plural(e.tx_total, "transaction")} reported.`);
    return parts.join(" ");
  }

  function linkTable(links) {
    const head = [["Name"], ["Role"], ["Title"], ["First filed"], ["Latest filed"], ["Filings", "num"]];
    return el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        {},
        el("thead", {}, el("tr", {}, head.map(([title, cls]) => el("th", { scope: "col", class: cls }, title)))),
        el(
          "tbody",
          {},
          links.map((l) =>
            el(
              "tr",
              {},
              el("td", {}, entityLink(l.ent)),
              el("td", {}, l.roles.join(", ") || "—"),
              el("td", {}, l.title || ""),
              el("td", {}, l.first),
              el("td", {}, l.last),
              el("td", { class: "num" }, fmtCount.format(l.filings)),
            ),
          ),
        ),
      ),
    );
  }

  async function renderEntity(cik) {
    const e = await getJSON(`data/e/${cik}.json`);
    document.title = `${e.display} · EDGAR Graph`;
    const insiders = e.links.filter((l) => l.dir === "insider");
    const insiderOf = e.links.filter((l) => l.dir === "insider_of");
    const cols = [
      "date",
      ...(insiderOf.length ? ["issuer"] : []),
      ...(insiders.length ? ["owner"] : []),
      "code",
      "shares",
      "price",
      "value",
      "after",
      "notes",
      "source",
    ];
    setView(
      el(
        "header",
        { class: "entity-head" },
        el("p", { class: `kind kind-${e.kind}` }, KIND[e.kind] || e.kind),
        el("h1", {}, e.display, e.ticker ? el("span", { class: "tick big" }, e.ticker) : null),
        e.name !== e.display ? el("p", { class: "muted" }, `Name as filed: ${e.name}`) : null,
        el("p", { class: "summary" }, summary(e, insiders, insiderOf)),
        el("p", { class: "muted small" }, `SEC CIK ${e.cik} · `, external(e.edgar, "All of its filings on sec.gov")),
      ),
      connections(e),
      insiders.length
        ? section(
            "Insiders",
            "Directors, officers, 10% owners and other insiders who filed Forms 3, 4 or 5 about this company.",
            linkTable(insiders),
          )
        : null,
      insiderOf.length
        ? section(
            "Insider of",
            "Companies this filer reported on as a director, officer, 10% owner or other insider.",
            linkTable(insiderOf),
          )
        : null,
      section(
        "Transactions",
        e.tx_total > e.tx.length ? `The latest ${fmtCount.format(e.tx.length)} of ${fmtCount.format(e.tx_total)}.` : null,
        e.tx.length
          ? tradeTable(e.tx, cols)
          : el("p", { class: "muted" }, "No transactions in the loaded filings. A Form 3 lists holdings only."),
      ),
    );
  }

  // ---------- connections map ----------

  function spread(list, gap) {
    // Nudge angles apart until neighbours are at least `gap` radians apart.
    if (list.length < 2) return;
    list.sort((p, q) => p.a - q.a);
    for (let pass = 0; pass < 60; pass += 1) {
      let moved = false;
      for (let i = 0; i < list.length; i += 1) {
        const p = list[i];
        const q = list[(i + 1) % list.length];
        const diff = i === list.length - 1 ? q.a + 2 * Math.PI - p.a : q.a - p.a;
        if (diff < gap - 1e-6) {
          const push = (gap - diff) / 2;
          p.a -= push;
          q.a += push;
          moved = true;
        }
      }
      if (!moved) break;
    }
  }

  function shape(n) {
    const size = n.ring === 0 ? 12 : n.ring === 1 ? 8 : 6;
    if (n.kind === "person") return svgEl("circle", { cx: n.x, cy: n.y, r: size, class: "shape" });
    if (n.kind === "company") {
      return svgEl("rect", { x: n.x - size, y: n.y - size, width: 2 * size, height: 2 * size, rx: 3, class: "shape" });
    }
    const d = size * 1.3;
    const points = `${n.x},${n.y - d} ${n.x + d},${n.y} ${n.x},${n.y + d} ${n.x - d},${n.y}`;
    return svgEl("polygon", { points, class: "shape" });
  }

  function nodeLabel(n, visible) {
    const cls = visible ? "lbl" : "lbl hover-only";
    if (n.ring === 0) {
      return svgEl("text", { x: n.x, y: n.y + 32, "text-anchor": "middle", class: `${cls} lbl-center` }, cut(n.label, 40));
    }
    const cos = Math.cos(n.a);
    const sin = Math.sin(n.a);
    const gap = n.ring === 1 ? 13 : 11;
    const anchor = Math.abs(cos) < 0.05 ? "middle" : cos > 0 ? "start" : "end";
    const dy = Math.abs(cos) > 0.3 ? 4 : sin > 0 ? 16 : -10;
    const text = cut(n.label, n.ring === 1 ? 16 : 22);
    return svgEl("text", { x: n.x + cos * gap, y: n.y + sin * gap + dy, "text-anchor": anchor, class: cls }, text);
  }

  function connections(e) {
    const { nodes, edges, left_out: leftOut } = e.graph;
    if (nodes.length < 2) return null;
    const W = 840;
    const H = 620;
    const byId = new Map(nodes.map((n) => [n.id, { ...n }]));
    const place = (n, r) => {
      n.x = W / 2 + r * Math.cos(n.a);
      n.y = H / 2 + r * Math.sin(n.a);
    };
    const center = byId.get(e.cik);
    Object.assign(center, { x: W / 2, y: H / 2, a: 0 });
    const ring1 = [...byId.values()].filter((n) => n.ring === 1);
    const ring2 = [...byId.values()].filter((n) => n.ring === 2);
    ring1.forEach((n, i) => {
      n.a = -Math.PI / 2 + (2 * Math.PI * i) / ring1.length;
      place(n, 125);
    });
    const parentAngles = new Map();
    for (const [a, b] of edges) {
      const p = byId.get(a);
      const q = byId.get(b);
      if (p && q && p.ring === 1 && q.ring === 2) parentAngles.set(q.id, [...(parentAngles.get(q.id) || []), p.a]);
    }
    for (const n of ring2) {
      const angles = parentAngles.get(n.id) || [0];
      n.a = Math.atan2(
        angles.reduce((s, a) => s + Math.sin(a), 0),
        angles.reduce((s, a) => s + Math.cos(a), 0),
      );
    }
    spread(ring2, Math.min((2 * Math.PI) / Math.max(ring2.length, 1), 0.3));
    ring2.forEach((n) => place(n, 255));

    // Fit the view to the drawn nodes, leaving room for side labels.
    const xs = [...byId.values()].map((n) => n.x);
    const ys = [...byId.values()].map((n) => n.y);
    let left = Math.min(...xs) - 150;
    let right = Math.max(...xs) + 150;
    const top = Math.min(...ys) - 40;
    const bottom = Math.max(...ys) + 50;
    if (right - left < 560) {
      const grow = (560 - (right - left)) / 2;
      left -= grow;
      right += grow;
    }
    const svg = svgEl("svg", {
      viewBox: `${left} ${top} ${right - left} ${bottom - top}`,
      class: "map",
      role: "img",
      "aria-label": `Connections of ${e.display}`,
    });
    const lines = svgEl("g", { class: "edges" });
    for (const [a, b] of edges) {
      const p = byId.get(a);
      const q = byId.get(b);
      if (!p || !q) continue;
      const cls = p.ring === 0 || q.ring === 0 ? "edge e1" : "edge e2";
      lines.append(svgEl("line", { x1: p.x, y1: p.y, x2: q.x, y2: q.y, class: cls }));
    }
    svg.append(lines);
    const showRing = { 0: true, 1: ring1.length <= 12, 2: ring2.length <= 14 };
    for (const n of [...ring2, ...ring1, center]) {
      const group =
        n.ring === 0
          ? svgEl("g", { class: `node center k-${n.kind}` })
          : svgEl("a", { href: href(n.id), class: `node k-${n.kind}` });
      group.append(svgEl("title", {}, `${n.label} (${KIND[n.kind] || n.kind})`), shape(n), nodeLabel(n, showRing[n.ring]));
      svg.append(group);
    }
    const legend = el(
      "figcaption",
      { class: "legend" },
      el("span", { class: "sw sw-person" }, "●"),
      " Person   ",
      el("span", { class: "sw sw-company" }, "■"),
      " Company   ",
      el("span", { class: "sw sw-organization" }, "◆"),
      " Fund or organization",
      leftOut ? ` · ${plural(leftOut, "more connection")} not drawn` : null,
      showRing[1] && showRing[2] ? null : " · Hover a shape for its name",
    );
    return section(
      "Connections",
      "Each line is an insider relationship reported in the loaded filings. Click a shape to go to that company or person.",
      el("figure", { class: "map-wrap" }, svg, legend),
    );
  }

  // ---------- about ----------

  async function renderAbout() {
    const meta = await getJSON("data/meta.json");
    document.title = "About · EDGAR Graph";
    const li = (...parts) => el("li", {}, ...parts);
    setView(
      el(
        "article",
        { class: "about" },
        el("h1", {}, "About this site"),
        el(
          "p",
          {},
          "Directors, officers and anyone owning more than 10% of a US public company are its insiders. They must " +
            "tell the SEC when they buy, sell, receive or give away the company's stock, on Forms 3, 4 and 5, which " +
            "the SEC publishes on EDGAR. This site links those filings into one graph: companies, the people and funds " +
            "who report as their insiders, and what each of them reported.",
        ),
        el("h2", {}, "Where the data comes from"),
        el(
          "p",
          {},
          "The SEC's ",
          external(
            "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets",
            "insider transactions data sets",
          ),
          `, which hold every Form 3, 4 and 5 as filed. Loaded here: ${meta.quarters.join(", ")} (filed ` +
            `${meta.first_filed} to ${meta.last_filed}). Each transaction links to its filing on sec.gov.`,
        ),
        el("h2", {}, "Reading the tables"),
        el(
          "ul",
          {},
          li(
            "A purchase (code P) or sale (code S) is a trade on the market or in a private deal. Grants (A), shares " +
              "withheld for taxes (F), option and RSU exercises (M) and gifts (G) are routine and labeled as such.",
          ),
          li("Since April 2023, filers tick a box when a trade was made under a pre-arranged Rule 10b5-1 trading plan. Those trades are marked “10b5-1 plan”."),
          li("“Indirect” means the shares are held through a trust, family member, fund or similar arrangement."),
          li("Value is shares times the reported price. Filers often report a weighted average across several trades."),
          li("A line on a connections map means the filer reported as an insider of that company in at least one loaded filing."),
        ),
        el("h2", {}, "Transaction codes"),
        el(
          "div",
          { class: "table-wrap" },
          el(
            "table",
            {},
            el("thead", {}, el("tr", {}, ["Code", "Short name", "Meaning"].map((h) => el("th", { scope: "col" }, h)))),
            el(
              "tbody",
              {},
              Object.entries(codes).map(([code, info]) =>
                el("tr", {}, el("td", {}, el("b", {}, code)), el("td", {}, info.label), el("td", {}, info.description)),
              ),
            ),
          ),
        ),
        el("h2", {}, "Limits"),
        el(
          "ul",
          {},
          li("Facts are shown as filed. Filings can contain mistakes, and this site doesn't correct or interpret them. A transaction listed here is not evidence of wrongdoing."),
          li(
            "People and organizations are identified only by their SEC ID (CIK), never by matching names. Readable " +
              "names such as “Timothy D Cook” are generated from the filed name (“COOK TIMOTHY D”), which each page also shows.",
          ),
          li("Amended filings are marked “amended” and listed beside the filing they correct. The largest-trade lists leave amendments out so a trade isn't counted twice."),
          li(
            "Prices are typed by hand, and a total typed into the price box can turn a small sale into a billion-dollar " +
              "one. The largest-trade lists only include trades priced within 3× of the median price at least three " +
              "other insiders of the same company reported. Trades far outside that range are marked “unusual price” " +
              "everywhere else on the site. A trade filed twice by the same insider is listed once.",
          ),
          li("Only the loaded quarters are covered. Someone who filed nothing in that period doesn't appear."),
          li("Filings include street addresses. This site never stores or shows them."),
          li("Nothing here is investment advice."),
        ),
      ),
    );
  }

  // ---------- search ----------

  async function loadIndex() {
    if (!index) {
      const rows = await getJSON("data/search.json");
      index = rows.map(([cik, display, kind, ticker]) => ({
        cik,
        display,
        kind,
        ticker,
        words: `${display} ${ticker || ""}`.toLowerCase().split(/[^\p{L}\p{N}.&'-]+/u).filter(Boolean),
      }));
    }
    return index;
  }

  async function onSearch() {
    const query = input.value.trim().toLowerCase();
    if (query.length < 2) {
      closeResults();
      return;
    }
    const list = await loadIndex();
    if (input.value.trim().toLowerCase() !== query) return; // the user kept typing
    const terms = query.split(/\s+/);
    const exact = list.filter(
      (x) => String(x.cik) === query || (x.ticker && x.ticker.toLowerCase().split(/[\s,/]+/).includes(query)),
    );
    const rest = [];
    for (const x of list) {
      if (rest.length >= 12) break;
      if (!exact.includes(x) && terms.every((t) => x.words.some((w) => w.startsWith(t)))) rest.push(x);
    }
    shown = [...exact, ...rest].slice(0, 12);
    active = shown.length ? 0 : -1;
    renderResults();
  }

  function renderResults() {
    const items = shown.length
      ? shown.map((x, i) =>
          el(
            "li",
            {
              id: `opt-${i}`,
              role: "option",
              "aria-selected": String(i === active),
              onmousedown: (event) => {
                event.preventDefault();
                go(x.cik);
              },
            },
            el("span", { class: "r-name" }, x.display),
            x.ticker ? el("span", { class: "tick" }, x.ticker) : null,
            el("span", { class: "r-kind" }, KIND[x.kind] || x.kind),
          ),
        )
      : [el("li", { class: "muted", role: "option", "aria-disabled": "true" }, "No matches")];
    results.replaceChildren(...items);
    results.hidden = false;
    input.setAttribute("aria-expanded", "true");
    if (active >= 0) input.setAttribute("aria-activedescendant", `opt-${active}`);
    else input.removeAttribute("aria-activedescendant");
  }

  function closeResults() {
    results.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    shown = [];
    active = -1;
  }

  function go(cik) {
    closeResults();
    input.value = "";
    input.blur();
    location.hash = href(cik);
  }

  function onKey(event) {
    if (results.hidden || !shown.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      active = (active + (event.key === "ArrowDown" ? 1 : -1) + shown.length) % shown.length;
      renderResults();
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (active >= 0) go(shown[active].cik);
    } else if (event.key === "Escape") {
      closeResults();
    }
  }

  // ---------- routing ----------

  async function route() {
    const hash = location.hash || "#/";
    const match = hash.match(/^#\/e\/(\d+)$/);
    try {
      if (match) await renderEntity(Number(match[1]));
      else if (hash === "#/about") await renderAbout();
      else await renderHome();
    } catch (err) {
      console.error(err);
      setView(
        el(
          "section",
          { class: "block" },
          el("h1", {}, "Couldn't load this page"),
          el(
            "p",
            {},
            match
              ? "That company or person isn't in this export."
              : "The site's data didn't load. If you opened index.html straight from disk, serve the folder instead: edgar-graph serve public",
          ),
          el("p", {}, el("a", { href: "#/" }, "Back to the start")),
        ),
      );
    }
  }

  async function init() {
    input.addEventListener("input", onSearch);
    input.addEventListener("keydown", onKey);
    input.addEventListener("blur", closeResults);
    window.addEventListener("hashchange", route);
    try {
      codes = await getJSON("data/codes.json");
      const meta = await getJSON("data/meta.json");
      document.getElementById("meta-line").textContent =
        `Data: SEC insider data sets ${meta.quarters.join(", ")}; built ${meta.generated.slice(0, 10)}.`;
    } catch (err) {
      console.error(err);
    }
    route();
  }

  init();
})();
