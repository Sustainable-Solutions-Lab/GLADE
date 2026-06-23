// SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
// SPDX-License-Identifier: CC-BY-4.0
//
// Carbon Price Dial - interactive GLADE widget.
// Two sliders (carbon price + value of a statistical life-year) and a
// fixed/flexible-diet toggle drive a synced world map, net-emissions bar,
// cost-vs-emissions curve, diet strip, feed strip and a years-of-life-lost
// readout. Everything is computed live in the browser by evaluating the GLADE
// MLP surrogate directly (no precomputed scenarios, no interpolation).

Promise.all([
  d3.json("data/surrogate.json"),
  d3.json("data/regions.geojson"),
]).then(([data, geo]) => init(data, geo))
  .catch((err) => {
    document.getElementById("dial").insertAdjacentHTML(
      "beforeend", `<p style="color:#a6191e">Failed to load data: ${err}</p>`);
  });

// ---- base64 little-endian float32 -> Float32Array ----
function b64f32(s) {
  const bin = atob(s);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return new Float32Array(buf.buffer);
}

// ---- decode one mode's weights + per-field reconstruction structures ----
function prepMode(m, geo) {
  const logSet = new Set(m.logIndices);
  const layers = m.layers.map((l) => ({
    w: b64f32(l.w), b: b64f32(l.b), nIn: l.nIn, nOut: l.nOut,
  }));
  const fields = {};
  for (const [fn, fd] of Object.entries(m.fields)) {
    const keyIndex = new Map(fd.keys.map((k, i) => [k, i]));
    const featCol = geo.features.map((f) =>
      keyIndex.has(f.properties.region) ? keyIndex.get(f.properties.region) : -1);
    fields[fn] = {
      comp: b64f32(fd.components), mean: b64f32(fd.mean),
      nComp: fd.nComp, nKeys: fd.nKeys,
      scoreIdx: fd.scoreCols.map((c) => m.outMap[c]),
      featCol,
    };
  }
  return {
    ...m, logSet, layers, fields,
    scalerMean: b64f32(m.scalerMean), scalerScale: b64f32(m.scalerScale),
  };
}

// ---- MLP forward pass: raw input vector -> standardized output vector ----
function forward(M, x) {
  let h = new Float64Array(x.length);
  for (let i = 0; i < x.length; i++) {
    const v = M.logSet.has(i) ? Math.log(x[i]) : x[i];
    h[i] = (v - M.scalerMean[i]) / M.scalerScale[i];
  }
  for (let k = 0; k < M.layers.length; k++) {
    const L = M.layers[k], out = new Float64Array(L.nOut), last = k === M.layers.length - 1;
    for (let o = 0; o < L.nOut; o++) {
      let s = L.b[o];
      for (let i = 0; i < L.nIn; i++) s += h[i] * L.w[i * L.nOut + o];
      out[o] = last ? s : (s > 0 ? s : 0);  // ReLU on hidden layers only
    }
    h = out;
  }
  return h;
}

function init(data, geo) {
  const meta = data.meta;
  const modes = {};
  meta.modes.forEach((mode) => { modes[mode] = prepMode(data.modes[mode], geo); });
  const allModes = meta.modes;
  let mode = allModes.includes("flexible") ? "flexible" : allModes[0];
  let price = 0, yll = 0;
  let dietUnit = "g";

  const foodGroups = meta.foodGroups;       // [{key,label,color,animal}]
  const food2group = meta.foodToGroup;      // {food: group}
  const feedCats = meta.feedCats;           // [{key,label,color}]
  const groupColor = meta.mapGroups.map((g) => g.color);
  const cropFields = meta.cropGroupFields;  // ordered, aligned with mapGroups
  const landByFeat = geo.features.map((f) => meta.regionArea[f.properties.region] || Infinity);

  // Combined display ranges (max/min across modes) so scales don't jump on the
  // mode toggle. Cost-curve LINES are still redrawn per mode (mode-specific).
  const R = (() => {
    const rs = allModes.map((mm) => modes[mm].ranges);
    const mn = (k) => Math.min(...rs.map((r) => r[k]));
    const mx = (k) => Math.max(...rs.map((r) => r[k]));
    return {
      emiMin: mn("emiMin"), emiMax: mx("emiMax"),
      costMin: mn("costMin"), costMax: mx("costMax"),
      dietMaxG: mx("dietMaxG"), dietMaxKcal: mx("dietMaxKcal"),
      feedMax: mx("feedMax"), yllMin: mn("yllMin"), yllMax: mx("yllMax"),
    };
  })();

  const hasYll = (mm) => "value_per_yll" in modes[mm].sliders;

  // ===== core: evaluate the surrogate at the current operating point =====
  function evaluate(mm, ghgPrice, valueYll) {
    const M = modes[mm];
    const x = Float64Array.from(M.nominal);
    x[M.sliders.ghg_price.index] = ghgPrice;
    if ("value_per_yll" in M.sliders) x[M.sliders.value_per_yll.index] = valueYll;
    const std = forward(M, x);
    const out = (name) => { const e = M.outMap[name]; return e ? std[e.i] * e.s + e.m : 0; };
    return { M, std, out };
  }

  // Cost decomposition (production / resistance / scc / health), in bn USD.
  // resistance uses cv deviation from the lowest-price diet at the same YLL.
  function costParts(mm, out, valueYll) {
    const M = modes[mm];
    const scc = out("objective_breakdown.ghg_cost");
    const stab = out("objective_breakdown.production_stability")
      + out("objective_breakdown.diet_stability");
    const cv = out("objective_breakdown.consumer_values");
    const health = out("objective_breakdown.health_burden");
    const total = out("total_cost");
    // cv_ref: consumer value at the lowest carbon price (same YLL); resistance
    // measures the diet's deviation from that no-carbon-price starting point.
    const cvRef = evaluate(mm, M.sliders.ghg_price.min, valueYll)
      .out("objective_breakdown.consumer_values");
    const resistance = stab + (cv - cvRef);
    const production = total - scc - stab - cv - health;
    return { production, resistance, scc, health, total: production + resistance + scc + health };
  }

  function emissions(out) {
    const co2 = out("co2"), seq = out("sequestration");
    return {
      "Land-use change": (co2 - seq) / 1000,
      "Enteric & manure (CH4)": out("ch4") / 1000,
      "Fertilizer & residues (N2O)": out("n2o") / 1000,
      "Sequestration": seq / 1000,
    };
  }

  function dietOf(M, out, key) {
    const factor = key === "kcal" ? meta.kcalFactor : meta.gramsFactor;
    const prefix = key === "kcal" ? "foods_energy" : "foods";
    const g = {};
    foodGroups.forEach((fg) => { g[fg.key] = 0; });
    M.vectors[prefix].forEach((food) => {
      const grp = food2group[food];
      if (grp in g) g[grp] += Math.max(out(`${prefix}.${food}`), 0) * factor;
    });
    return g;
  }
  function feedOf(M, out) {
    const f = {};
    feedCats.forEach((fc) => { f[fc.key] = Math.max(out(`feed_categories.${fc.key}`), 0); });
    return f;
  }

  // Per-region map arrays (aligned to geo feature order).
  function fieldByFeature(M, std, field) {
    const f = M.fields[field], scores = new Float64Array(f.nComp);
    for (let c = 0; c < f.nComp; c++) scores[c] = std[f.scoreIdx[c].i] * f.scoreIdx[c].s + f.scoreIdx[c].m;
    const vals = new Float64Array(f.featCol.length);
    for (let fi = 0; fi < vals.length; fi++) {
      const col = f.featCol[fi];
      if (col < 0) { vals[fi] = 0; continue; }
      let s = f.mean[col];
      for (let c = 0; c < f.nComp; c++) s += scores[c] * f.comp[c * f.nKeys + col];
      vals[fi] = s;
    }
    return vals;
  }
  function mapArrays(M, std) {
    const crop = fieldByFeature(M, std, "cropland_by_region");
    const past = fieldByFeature(M, std, "grazing_by_region");
    const groupAreas = cropFields.map((fn) => fieldByFeature(M, std, fn));
    const n = crop.length;
    const regionGroup = new Array(n), regionIntensity = new Array(n), regionPasture = new Array(n);
    for (let i = 0; i < n; i++) {
      let best = -1, bestv = 0;
      for (let g = 0; g < groupAreas.length; g++) {
        if (groupAreas[g][i] > bestv) { bestv = groupAreas[g][i]; best = g; }
      }
      regionGroup[i] = bestv > 1e-6 ? best : -1;
      regionIntensity[i] = Math.max(0, Math.min(1, crop[i] / (meta.cropMaxFrac * landByFeat[i])));
      regionPasture[i] = Math.max(0, Math.min(1, past[i] / (meta.pastureMaxFrac * landByFeat[i])));
    }
    return { regionGroup, regionIntensity, regionPasture };
  }

  // Full scenario object consumed by the panels.
  function scenario(mm, ghgPrice, valueYll) {
    const { M, std, out } = evaluate(mm, ghgPrice, valueYll);
    const emi = emissions(out);
    // Net = sum of all four bars = (co2 + ch4 + n2o); the LUC bar already has
    // sequestration removed and the Sequestration bar adds it back.
    const net = emi["Land-use change"] + emi["Enteric & manure (CH4)"]
      + emi["Fertilizer & residues (N2O)"] + emi["Sequestration"];
    return {
      emissions: emi, netEmissions: net,
      costParts: costParts(mm, out, valueYll),
      diet: dietOf(M, out, "g"), dietKcal: dietOf(M, out, "kcal"),
      feed: feedOf(M, out), yll: out("yll"),
      ...mapArrays(M, std),
    };
  }

  // ================= panels (built once; updated on render) =================
  const MW = 720, MH = 360;
  const proj = d3.geoNaturalEarth1().fitExtent([[6, 6], [MW - 6, MH - 6]], geo);
  const path = d3.geoPath(proj);
  const PASTURE_COLOR = "#6a994e";
  function buildMap(sel) {
    const svg = d3.select(sel).attr("viewBox", `0 0 ${MW} ${MH}`);
    svg.append("path").attr("d", path({ type: "Sphere" }))
      .attr("fill", "#eef3f1").attr("stroke", "#dfe7e3");
    return svg.append("g").selectAll("path").data(geo.features).join("path")
      .attr("d", path).attr("stroke", "#fff").attr("stroke-width", 0.2);
  }
  const cropPaths = buildMap("#map");
  const pasturePaths = buildMap("#mapPasture");

  const legend = d3.select("#legend");
  meta.mapGroups.forEach((g) => {
    const it = legend.append("div").attr("class", "legend__item");
    it.append("span").attr("class", "legend__swatch").style("background", g.color);
    it.append("span").text(g.name);
  });
  document.getElementById("pastureBar").style.background =
    `linear-gradient(90deg, rgba(106,153,78,0.12), rgba(106,153,78,1))`;

  function updateMap(s) {
    cropPaths
      .attr("fill", (d, i) => s.regionGroup[i] < 0 ? "#e9eeec" : groupColor[s.regionGroup[i]])
      .attr("fill-opacity", (d, i) => s.regionGroup[i] < 0 ? 1 : 0.12 + 0.88 * s.regionIntensity[i]);
  }
  function updatePasture(s) {
    pasturePaths.attr("fill", PASTURE_COLOR)
      .attr("fill-opacity", (d, i) => { const v = s.regionPasture[i]; return v <= 0 ? 0.04 : 0.12 + 0.88 * v; });
  }

  // ---- emissions bar ----
  const cats = meta.emissionCategories;
  const EW = 330, EH = 170, em = { t: 12, r: 10, b: 28, l: 38 };
  const emiSvg = d3.select("#emiChart").attr("viewBox", `0 0 ${EW} ${EH}`);
  const exS = d3.scaleBand().domain(cats).range([em.l, EW - em.r]).padding(0.28);
  const eyS = d3.scaleLinear().domain([R.emiMin * 1.05, R.emiMax * 1.08]).range([EH - em.b, em.t]);
  emiSvg.append("g").attr("class", "axis").attr("transform", `translate(${em.l},0)`)
    .call(d3.axisLeft(eyS).ticks(4).tickSize(-(EW - em.l - em.r)))
    .call((g) => g.select(".domain").remove());
  emiSvg.append("line").attr("x1", em.l).attr("x2", EW - em.r)
    .attr("y1", eyS(0)).attr("y2", eyS(0)).attr("stroke", "#9aa8a2");
  const SHORT = { "Land-use change": "LUC", "Enteric & manure (CH4)": "CH₄",
    "Fertilizer & residues (N2O)": "N₂O", "Sequestration": "Seq." };
  emiSvg.append("g").selectAll("text").data(cats).join("text")
    .attr("x", (c) => exS(c) + exS.bandwidth() / 2).attr("y", EH - 9)
    .attr("text-anchor", "middle").style("font-size", "10px").style("fill", "#6b7a74")
    .text((c) => SHORT[c] || c);
  const emiBars = emiSvg.append("g").selectAll("rect").data(cats).join("rect")
    .attr("x", (c) => exS(c)).attr("width", exS.bandwidth())
    .attr("fill", (c) => c === "Sequestration" ? "#5fa285" : "#c2693f");
  function updateEmissions(s) {
    emiBars.attr("y", (c) => Math.min(eyS(s.emissions[c]), eyS(0)))
      .attr("height", (c) => Math.abs(eyS(s.emissions[c]) - eyS(0)));
    const net = s.netEmissions, nv = document.getElementById("netValue");
    nv.textContent = (net >= 0 ? "+" : "−") + Math.abs(net).toFixed(1);
    nv.parentElement.classList.toggle("is-negative", net < 0);
  }

  // ---- YLL readout ----
  function updateYll(s) {
    const el = document.getElementById("yllOut");
    if (el) el.textContent = hasYll(mode) ? s.yll.toFixed(1) : "—";
  }

  // ---- cost-vs-price curve (4 component lines + total, drawn live) ----
  const CW = 330, CH = 170, cm = { t: 14, r: 14, b: 30, l: 46 };
  const curveSvg = d3.select("#curveChart").attr("viewBox", `0 0 ${CW} ${CH}`);
  const costKeys = meta.costParts.map((c) => c.key);
  const costColor = {}; meta.costParts.forEach((c) => { costColor[c.key] = c.color; });
  const PMIN = modes[mode].sliders.ghg_price.min, PMAX = modes[mode].sliders.ghg_price.max;
  const xc = d3.scaleLog().domain([PMIN, PMAX]).range([cm.l, CW - cm.r]);
  const yc = d3.scaleLinear().domain([R.costMin * 1.06, R.costMax * 1.06]).range([CH - cm.b, cm.t]);
  curveSvg.append("g").attr("class", "axis").attr("transform", `translate(${cm.l},0)`)
    .call(d3.axisLeft(yc).ticks(5).tickFormat((d) => (d / 1000).toFixed(1) + "T"));
  curveSvg.append("g").attr("class", "axis").attr("transform", `translate(0,${CH - cm.b})`)
    .call(d3.axisBottom(xc).tickValues([5, 25, 100, 500].filter((d) => d >= PMIN && d <= PMAX)).tickFormat((d) => "$" + d));
  curveSvg.append("text").attr("x", (CW + cm.l) / 2).attr("y", CH - 4)
    .attr("text-anchor", "middle").style("font-size", "9.5px").style("fill", "#9aa8a2")
    .text("Carbon price (USD / t)");
  curveSvg.append("line").attr("x1", cm.l).attr("x2", CW - cm.r)
    .attr("y1", yc(0)).attr("y2", yc(0)).attr("stroke", "#9aa8a2");
  const compPath = {};
  costKeys.forEach((k) => {
    compPath[k] = curveSvg.append("path").attr("fill", "none")
      .attr("stroke", costColor[k]).attr("stroke-width", 1.6).attr("data-key", k);
  });
  const totalPath = curveSvg.append("path").attr("fill", "none")
    .attr("stroke", "#1f2a26").attr("stroke-width", 2.4);
  const costDot = curveSvg.append("circle").attr("r", 5)
    .attr("fill", "#1f2a26").attr("stroke", "#fff").attr("stroke-width", 2);

  const PRICE_SAMPLES = d3.range(0, 49).map((i) => PMIN * Math.pow(PMAX / PMIN, i / 48));
  function drawCostLines() {
    // Cost-only path (no map/diet/feed reconstruction) -- this runs 49x per
    // YLL-slider frame, so it must stay light.
    const pts = PRICE_SAMPLES.map((p) => {
      const { out } = evaluate(mode, p, yll);
      return { p, c: costParts(mode, out, yll) };
    });
    const ln = (acc) => d3.line().x((d) => xc(d.p)).y((d) => yc(acc(d)));
    costKeys.forEach((k) => compPath[k].datum(pts).attr("d", ln((d) => d.c[k]))
      .style("display", (k === "health" && !hasYll(mode)) ? "none" : null));
    totalPath.datum(pts).attr("d", ln((d) => d.c.total)).raise();
    costDot.raise();
  }
  const costLegItems = meta.costParts.concat([{ key: "objective", label: "Total (objective)", color: "#1f2a26" }]);
  const costLeg = d3.select("#costLegend").selectAll("div.strip-legend__item")
    .data(costLegItems, (d) => d.key).join("div")
    .attr("class", (d) => "strip-legend__item" + (d.key === "objective" ? " is-animal" : "")
      + (d.key === "health" ? " yll-only" : ""));
  costLeg.append("span").attr("class", "strip-legend__swatch").style("background", (d) => d.color);
  costLeg.append("span").attr("class", "strip-legend__label").text((d) => d.label);
  const costLegVals = costLeg.append("span").attr("class", "strip-legend__val");
  const fmtT = (v) => (v < 0 ? "-" : "") + Math.abs(v / 1000).toFixed(2) + "T";
  function updateCost(s) {
    costDot.attr("cx", xc(price)).attr("cy", yc(s.costParts.total));
    costLegVals.text((d) => fmtT(d.key === "objective" ? s.costParts.total : s.costParts[d.key]));
  }

  // ---- generic stacked horizontal strip (diet, feed) ----
  function makeStrip(svgId, legendId, items, maxByKey) {
    const DW = 1100, DH = 74, dm = { t: 6, r: 10, b: 24, l: 10 };
    const svg = d3.select(svgId).attr("viewBox", `0 0 ${DW} ${DH}`);
    const xByKey = {};
    for (const vk of Object.keys(maxByKey))
      xByKey[vk] = d3.scaleLinear().domain([0, maxByKey[vk] || 1]).range([dm.l, DW - dm.r]);
    const gBars = svg.append("g"), gLab = svg.append("g");
    const legItems = d3.select(legendId).selectAll("div.strip-legend__item")
      .data(items, (d) => d.key).join("div")
      .attr("class", (d) => "strip-legend__item" + (d.animal ? " is-animal" : ""));
    legItems.append("span").attr("class", "strip-legend__swatch").style("background", (d) => d.color);
    legItems.append("span").attr("class", "strip-legend__label").text((d) => d.label || d.key);
    const legVals = legItems.append("span").attr("class", "strip-legend__val");
    const fmt = (v) => (v >= 10 ? v.toFixed(0) : v.toFixed(1));
    return (vals, vk) => {
      const x = xByKey[vk]; let acc = 0;
      const segs = items.map((it) => {
        const v = vals[it.key] || 0; const seg = { ...it, x0: acc, x1: acc + v }; acc += v; return seg;
      });
      gBars.selectAll("rect").data(segs, (d) => d.key).join("rect")
        .attr("x", (d) => x(d.x0)).attr("y", dm.t)
        .attr("width", (d) => Math.max(0, x(d.x1) - x(d.x0))).attr("height", DH - dm.t - dm.b)
        .attr("fill", (d) => d.color).attr("stroke", "#fff").attr("stroke-width", 1);
      gLab.selectAll("text").data(segs.filter((d) => x(d.x1) - x(d.x0) > 42), (d) => d.key)
        .join("text").attr("x", (d) => (x(d.x0) + x(d.x1)) / 2).attr("y", DH - 8)
        .attr("text-anchor", "middle").style("font-size", "11px")
        .style("font-weight", (d) => d.animal ? 700 : 500).style("fill", "#475650")
        .text((d) => d.label || d.key);
      legVals.text((d) => fmt(vals[d.key] || 0));
    };
  }
  const dietItems = foodGroups.map((g) => ({ key: g.key, label: g.label, color: g.color, animal: g.animal }));
  const feedItems = feedCats.map((f) => ({ key: f.key, label: f.label, color: f.color, animal: false }));
  const updateDiet = makeStrip("#dietChart", "#dietLegend", dietItems,
    { g: R.dietMaxG, kcal: R.dietMaxKcal });
  const updateFeed = makeStrip("#feedChart", "#feedLegend", feedItems, { feed: R.feedMax });

  // ================= sliders =================
  function logSlider(sliderId, ticksId, smin, smax, ticks) {
    const lr = Math.log(smax / smin);
    const posToVal = (p) => smin * Math.exp(lr * p);
    const valToPos = (v) => Math.log(v / smin) / lr;
    d3.select(ticksId).selectAll("span")
      .data(ticks.filter((d) => d >= smin && d <= smax)).join("span")
      .style("left", (d) => `${valToPos(d) * 100}%`).text((d) => "$" + d);
    return { posToVal, valToPos, smin, smax };
  }
  const RES = 1000;
  const priceCfg = modes[mode].sliders.ghg_price;
  const priceScale = logSlider("#ticks", "#ticks", priceCfg.min, priceCfg.max, [5, 10, 25, 50, 100, 200, 500]);
  const yllCfgMode = allModes.find(hasYll);
  const yllCfg = yllCfgMode ? modes[yllCfgMode].sliders.value_per_yll : null;
  const yllScale = yllCfg
    ? logSlider("#yllSlider", "#yllTicks", yllCfg.min, yllCfg.max, [50, 200, 1000, 5000, 20000, 50000])
    : null;

  const slider = document.getElementById("slider");
  const yllSlider = document.getElementById("yllSlider");
  const priceValue = document.getElementById("priceValue");
  const yllValue = document.getElementById("yllValue");
  const dietHint = document.getElementById("dietHint");

  function render() {
    const s = scenario(mode, price, yll);
    priceValue.textContent = Math.round(price);
    if (yllValue) yllValue.textContent = yll ? Math.round(yll).toLocaleString() : "–";
    dietHint.textContent = (dietUnit === "kcal" ? "kcal / person / day" : "g / person / day")
      + (mode === "fixed" ? " - held at the 2020 baseline in this mode" : " - plant-based to animal-based");
    updateMap(s); updatePasture(s); updateEmissions(s); updateCost(s);
    updateDiet(dietUnit === "kcal" ? s.dietKcal : s.diet, dietUnit === "kcal" ? "kcal" : "g");
    updateFeed(s.feed, "feed"); updateYll(s);
  }

  slider.addEventListener("input", (e) => { price = priceScale.posToVal(+e.target.value / RES); render(); });
  if (yllSlider && yllScale) {
    yllSlider.addEventListener("input", (e) => { yll = yllScale.posToVal(+e.target.value / RES); drawCostLines(); render(); });
  }

  // ---- mode toggle ----
  function applyModeClass() {
    document.getElementById("dial").classList.toggle("mode-no-yll", !hasYll(mode));
    if (yllSlider) yllSlider.disabled = !hasYll(mode);
  }
  const syncModeButtons = () =>
    d3.selectAll("#modeToggle .toggle__btn").classed("is-active", function () { return this.dataset.mode === mode; });
  d3.selectAll("#modeToggle .toggle__btn").each(function () {
    if (!allModes.includes(this.dataset.mode)) { this.disabled = true; this.style.opacity = 0.4; }
  });
  if (allModes.length > 1) {
    document.getElementById("modeToggle").addEventListener("click", () => {
      mode = allModes.find((m) => m !== mode) || mode;
      syncModeButtons(); applyModeClass(); drawCostLines(); render();
    });
  }

  // ---- diet unit toggle ----
  const syncUnitButtons = () =>
    d3.selectAll("#dietUnitToggle .unit-toggle__btn").classed("is-active", function () { return this.dataset.unit === dietUnit; });
  document.getElementById("dietUnitToggle").addEventListener("click", () => {
    dietUnit = dietUnit === "kcal" ? "g" : "kcal"; syncUnitButtons(); render();
  });

  // ---- deep link: ?price=200&yll=5000&mode=flexible&unit=kcal ----
  const q = new URLSearchParams(location.search);
  if (allModes.includes(q.get("mode"))) mode = q.get("mode");
  price = Math.max(priceCfg.min, Math.min(priceCfg.max, +q.get("price") || priceCfg.min));
  yll = yllCfg ? Math.max(yllCfg.min, Math.min(yllCfg.max, +q.get("yll") || yllCfg.min)) : 0;
  if (q.get("unit") === "kcal") dietUnit = "kcal";

  slider.value = Math.round(priceScale.valToPos(price) * RES);
  if (yllSlider && yllScale) yllSlider.value = Math.round(yllScale.valToPos(yll) * RES);
  syncModeButtons(); syncUnitButtons(); applyModeClass(); drawCostLines(); render();
}
