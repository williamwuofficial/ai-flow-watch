/* AI Flow Watch — renders data/dashboard.json with Observable Plot. */
(() => {
  const STATE = {
    red: { label: "Reversing", icon: "▼" },
    amber: { label: "Weakening", icon: "◆" },
    green: { label: "No reversal", icon: "▲" },
    grey: { label: "Collecting", icon: "○" },
  };
  // Signal 6 measures fragility, not a trend, so it gets its own wording.
  const LABEL_OVERRIDES = { 6: { green: "Normal", amber: "Elevated" } };
  const stateFor = (g) => ({ ...STATE[g.state], label: LABEL_OVERRIDES[g.id]?.[g.state] || STATE[g.state].label });
  let DATA = null;
  let rangeDays = 365;

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const fmtPct = (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`);
  const fmtUsd = (v) => {
    if (v == null) return "—";
    const a = Math.abs(v), s = a >= 1e9 ? `${(a / 1e9).toFixed(1)}B` : `${(a / 1e6).toFixed(0)}M`;
    return `${v < 0 ? "−" : "+"}$${s}`;
  };

  // columns {date:[...], k:[...]} → rows, filtered to the selected range
  function rows(cols, keys) {
    if (!cols) return [];
    const dates = cols.date.map((d) => new Date(d + "T00:00:00Z"));
    const cutoff = rangeDays ? new Date(dates[dates.length - 1] - rangeDays * 864e5) : null;
    const out = [];
    dates.forEach((d, i) => {
      if (cutoff && d < cutoff) return;
      const r = { date: d };
      keys.forEach((k) => (r[k] = cols[k][i]));
      out.push(r);
    });
    return out;
  }

  function legend(items) {
    const el = document.createElement("div");
    el.className = "legend";
    el.innerHTML = items.map((s) => `<span><i style="background:${s.color};height:${s.width || 2}px"></i>${s.label}</span>`).join("");
    return el;
  }

  /**
   * Line chart on one axis. series: [{key, label, color, width, opacity}]
   * opts: {zero, yLabel, fmt, title}
   */
  function lineChart(el, data, series, opts = {}) {
    el.replaceChildren();
    const pts = data.filter((r) => series.some((s) => r[s.key] != null));
    if (!pts.length) {
      el.innerHTML = `<div class="empty">${opts.empty || "Not enough data yet — this series builds up as the site collects daily snapshots."}</div>`;
      return;
    }
    if (opts.title) {
      const h = document.createElement("h3");
      h.textContent = opts.title;
      el.append(h);
    }
    if (series.length > 1) el.append(legend(series));
    const long = series.flatMap((s) => pts.filter((r) => r[s.key] != null).map((r) => ({ date: r.date, v: r[s.key], s: s.label })));
    const fmt = opts.fmt || ((v) => v.toFixed(2));
    const colorOf = Object.fromEntries(series.map((s) => [s.label, s.color]));
    const width = el.clientWidth || 800;
    const marks = [
      Plot.gridY({ stroke: css("--grid"), strokeOpacity: 1 }),
      opts.zero ? Plot.ruleY([0], { stroke: css("--axis") }) : null,
      ...series.map((s) =>
        Plot.lineY(long.filter((d) => d.s === s.label), {
          x: "date", y: "v", stroke: s.color, strokeWidth: s.width || 2, strokeOpacity: s.opacity ?? 1, curve: "linear",
        })
      ),
      Plot.ruleX(long, Plot.pointerX({ x: "date", stroke: css("--axis") })),
      Plot.dot(long, Plot.pointerX({ x: "date", y: "v", fill: (d) => colorOf[d.s], r: 4, stroke: css("--surface"), strokeWidth: 2 })),
      Plot.tip(
        pts,
        Plot.pointerX({
          x: "date",
          y: (r) => r[series[0].key],
          title: (r) =>
            [r.date.toISOString().slice(0, 10), ...series.map((s) => `${s.label}: ${r[s.key] == null ? "—" : fmt(r[s.key])}`)].join("\n"),
          fill: css("--surface"), stroke: css("--axis"), textColor: css("--ink"),
        })
      ),
    ].filter(Boolean);
    const plot = Plot.plot({
      width,
      height: opts.height || (width < 560 ? 220 : 280),
      marginLeft: 52,
      marginRight: 12,
      style: { background: "transparent", color: css("--muted"), fontFamily: css("--font"), fontSize: "12px" },
      x: { type: "utc", label: null, line: false, tickSize: 0 },
      y: { label: opts.yLabel || null, tickFormat: opts.tickFmt, tickSize: 0, nice: true, zero: !!opts.zeroDomain },
      marks,
    });
    el.append(plot);
  }

  function signalCards() {
    const s = DATA.summary;
    document.getElementById("summary").innerHTML =
      `<strong>${s.red} of ${DATA.signals.length}</strong> reversing, ${s.amber} weakening, ${s.green} show no reversal` +
      (s.grey ? `, ${s.grey} still collecting data.` : ".");
    document.getElementById("cards").innerHTML = DATA.signals
      .map((g) => {
        const st = stateFor(g);
        return `<a class="card ${g.state}" href="#${g.chart}">
          <div class="top"><span class="name">${g.id}. ${g.name}</span>
          <span class="badge ${g.state}" aria-label="status ${st.label}"><span aria-hidden="true">${st.icon}</span>${st.label}</span></div>
          <div class="value">${g.value}</div>
          <div class="detail">${g.detail}</div></a>`;
      })
      .join("");
  }

  function tables() {
    const cov = DATA.coverage.slice().sort((a, b) => a.group.localeCompare(b.group) || (b.aum || 0) - (a.aum || 0));
    document.getElementById("coverage").innerHTML =
      "<thead><tr><th>Fund</th><th>Group</th><th>Source</th><th>Type</th><th>History from</th><th>Latest</th><th class=num>Days of flows</th><th class=num>AUM</th></tr></thead><tbody>" +
      cov
        .map((c) => {
          const kind = c.source === "ssga" || c.source === "proshares" ? "Full history" : c.estimated ? "Snapshot (estimated)" : "Snapshot";
          const aum = c.aum == null ? "—" : `$${(c.aum / 1e9).toFixed(1)}B`;
          return `<tr><td>${c.ticker}</td><td>${c.group}</td><td>${c.source}</td><td>${kind}</td><td>${c.first}</td><td>${c.last}</td><td class=num>${c.days}</td><td class=num>${aum}</td></tr>`;
        })
        .join("") +
      "</tbody>";
    // collapse per-ticker price entries into one line
    const groups = {};
    for (const [k, v] of Object.entries(DATA.status)) {
      const src = k.split(":")[0];
      const g = (groups[src] ||= { ok: 0, fail: [], last: null });
      if (v.ok) g.ok++;
      else g.fail.push(`${k.split(":")[1] || src}: ${v.error}`);
      if (v.last_success && (!g.last || v.last_success > g.last)) g.last = v.last_success;
    }
    document.getElementById("status").innerHTML =
      "<thead><tr><th>Source</th><th>Status</th><th>Last success (UTC)</th><th>Errors</th></tr></thead><tbody>" +
      Object.entries(groups)
        .map(([src, g]) => {
          const st = g.fail.length ? (g.ok ? "amber" : "red") : "green";
          const label = g.fail.length ? `${g.fail.length} failing` : "OK";
          return `<tr><td>${src}</td><td><span class="badge ${st}"><span aria-hidden="true">${STATE[st].icon}</span>${label}</span></td><td>${(g.last || "—").replace("T", " ").slice(0, 16)}</td><td style="white-space:normal">${g.fail.join("<br>") || ""}</td></tr>`;
        })
        .join("") +
      "</tbody>";
  }

  function charts() {
    const S = DATA.series;
    const c1 = css("--series-1"), c2 = css("--series-2"), c3 = css("--series-3"), mute = css("--muted");
    const q = (k) => document.querySelector(`[data-chart="${k}"]`);
    const pctTick = (v) => `${v}%`;

    for (const g of ["flows_broad", "flows_ai_theme", "flows_leveraged"]) {
      lineChart(q(g), rows(S[g], ["s20", "s60"]), [
        { key: "s20", label: "20-day net flow, % of AUM", color: c1 },
        { key: "s60", label: "60-day", color: mute, width: 1.5 },
      ], { zero: true, fmt: fmtPct, tickFmt: pctTick });
    }

    for (const [k, title] of [["spy_rsp", "SPY / RSP (S&P 500 cap vs equal weight)"], ["qqq_qqew", "QQQ / QQEW (Nasdaq-100 cap vs equal weight)"]]) {
      lineChart(q(k), rows(S.cap_vs_equal, [k, `${k}_ma50`, `${k}_ma200`]), [
        { key: k, label: "Ratio", color: c1 },
        { key: `${k}_ma50`, label: "50-day avg", color: c2, width: 1.5 },
        { key: `${k}_ma200`, label: "200-day avg", color: mute, width: 1.5 },
      ], { title, fmt: (v) => v.toFixed(3) });
    }

    lineChart(q("ai_basket"), rows(S.ai_basket, ["ai_vs_rsp", "ma50", "ma200"]), [
      { key: "ai_vs_rsp", label: "AI basket ÷ RSP", color: c1 },
      { key: "ma50", label: "50-day avg", color: c2, width: 1.5 },
      { key: "ma200", label: "200-day avg", color: mute, width: 1.5 },
    ], { fmt: (v) => v.toFixed(3) });

    lineChart(q("impact"), rows(S.impact, ["slope"]), [{ key: "slope", label: "Return per 1% of AUM flow", color: c1 }], {
      zero: true, fmt: (v) => `${v.toFixed(2)}×`, yLabel: "Slope (return % per flow %)",
    });

    lineChart(q("cftc"), rows(S.cftc, ["combined", "median52", "lev_combined"]), [
      { key: "combined", label: "Asset managers net long", color: c1 },
      { key: "median52", label: "Asset managers 52-week median (roll weeks excluded)", color: mute, width: 1.5 },
      { key: "lev_combined", label: "Hedge funds net", color: c2, width: 1.5 },
    ], { zero: true, fmt: (v) => `${v.toFixed(1)}%`, tickFmt: pctTick });

    lineChart(q("twse"), rows(S.twse, ["s20"]), [{ key: "s20", label: "20-day foreign net buy (bn TWD)", color: c1 }], {
      zero: true, fmt: (v) => `${v.toFixed(0)}bn`, empty: "Backfilling Taiwan history — check back after the next daily run.",
    });

    lineChart(q("kospi"), rows(S.kospi, ["s20"]), [{ key: "s20", label: "20-day foreign net buy (tn KRW)", color: c1 }], {
      zero: true, fmt: (v) => `${v.toFixed(1)}tn`, empty: "Backfilling Korea history — check back after the next daily run.",
    });

    // index to 100 at the start of the visible range
    const ix = rows(S.index, ["SPY", "QQQ", "SMH"]);
    if (ix.length) {
      const base = { ...ix[0] }; // copy: rows are rebased in place
      ix.forEach((r) => ["SPY", "QQQ", "SMH"].forEach((k) => (r[k] = r[k] == null ? null : (r[k] / base[k]) * 100)));
    }
    lineChart(q("index"), ix, [
      { key: "SPY", label: "SPY", color: c1 },
      { key: "QQQ", label: "QQQ", color: c2 },
      { key: "SMH", label: "SMH (semis)", color: c3 },
    ], { fmt: (v) => v.toFixed(1) });

    for (const k of ["BAMLH0A0HYM2", "DGS10", "DTWEXBGS"]) {
      lineChart(q(k), rows(S.macro, [k]), [{ key: k, label: k, color: c1 }], { height: 180, fmt: (v) => v.toFixed(2) });
    }
  }

  function render() {
    if (!DATA) return;
    charts();
  }

  document.querySelectorAll(".toolbar button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".toolbar button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      rangeDays = +b.dataset.range;
      render();
    })
  );
  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(render, 150); });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);

  fetch("data/dashboard.json", { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then((d) => {
      DATA = d;
      const gen = new Date(d.generated);
      document.getElementById("meta").textContent =
        `Updated ${gen.toUTCString().replace(":00 GMT", " UTC")} · data through ${d.series.index.date.at(-1)}`;
      signalCards();
      tables();
      render();
    })
    .catch((e) => (document.getElementById("meta").textContent = `Could not load data (${e.message}).`));
})();
