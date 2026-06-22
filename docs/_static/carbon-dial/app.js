// SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
// SPDX-License-Identifier: CC-BY-4.0
//
// Carbon Price Dial - interactive GLADE widget.
// A carbon-price slider + a fixed/flexible-diet toggle drive a synced world
// map, net-emissions bar, cost-vs-emissions curve, diet strip and feed strip,
// interpolating between the paper's published GHG-price scenarios.

Promise.all([
  d3.json("data/data.json"),
  d3.json("data/regions.geojson"),
]).then(([data, geo]) => init(data, geo))
  .catch((err) => {
    document.getElementById("dial").insertAdjacentHTML(
      "beforeend", `<p style="color:#a6191e">Failed to load data: ${err}</p>`);
  });

const lerp = (a, b, t) => a + (b - a) * t;

function init(data, geo) {
  const meta = data.meta;
  const groupColor = meta.mapGroups.map((g) => g.color);
  const foodGroups = meta.foodGroups;        // [{key,label,color,animal}]
  const feedCats = meta.feedCats;            // [{key,color}]
  const allModes = meta.modes;               // present modes only
  let mode = allModes[0];
  let price = 0;

  if (meta.synthetic) {
    document.getElementById("syntheticFlag").hidden = false;
    document.getElementById("footNote").textContent = "placeholder data";
  }

  const allScen = allModes.flatMap((m) => data.modes[m].scenarios);

  // ---- interpolation within a mode ----
  function interp(m, p) {
    const sc = data.modes[m].scenarios;
    const px = data.modes[m].prices;
    let i = 0;
    while (i < px.length - 1 && px[i + 1] < p) i++;
    const j = Math.min(i + 1, px.length - 1);
    let t = px[j] === px[i] ? 0 : (p - px[i]) / (px[j] - px[i]);
    t = Math.max(0, Math.min(1, t));  // never extrapolate beyond the sweep
    const a = sc[i], b = sc[j];
    const dictLerp = (da, db) => {
      const o = {};
      for (const k of new Set([...Object.keys(da), ...Object.keys(db)]))
        o[k] = lerp(da[k] ?? 0, db[k] ?? 0, t);
      return o;
    };
    const near = t < 0.5 ? a : b;
    return {
      emissions: dictLerp(a.emissions, b.emissions),
      netEmissions: lerp(a.netEmissions, b.netEmissions, t),
      objective: lerp(a.objective, b.objective, t),
      costParts: dictLerp(a.costParts, b.costParts),
      diet: dictLerp(a.diet, b.diet),
      dietKcal: dictLerp(a.dietKcal || {}, b.dietKcal || {}),
      feed: dictLerp(a.feed, b.feed),
      regionGroup: near.regionGroup,
      regionIntensity: a.regionIntensity.map((v, k) =>
        lerp(v, b.regionIntensity[k], t)),
      regionPasture: a.regionPasture.map((v, k) =>
        lerp(v, b.regionPasture[k], t)),
    };
  }

  // ---- MAPS (cropland: crop-group colour; pasture: monochrome shade) ----
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
      .attr("fill-opacity", (d, i) =>
        s.regionGroup[i] < 0 ? 1 : 0.12 + 0.88 * s.regionIntensity[i]);
  }
  function updatePasture(s) {
    pasturePaths.attr("fill", PASTURE_COLOR)
      .attr("fill-opacity", (d, i) => {
        const v = s.regionPasture[i];
        return v <= 0 ? 0.04 : 0.12 + 0.88 * v;
      });
  }

  // ---- EMISSIONS BAR ----
  const cats = meta.emissionCategories;
  const EW = 330, EH = 170, em = { t: 12, r: 10, b: 28, l: 38 };
  const emiSvg = d3.select("#emiChart").attr("viewBox", `0 0 ${EW} ${EH}`);
  const exS = d3.scaleBand().domain(cats).range([em.l, EW - em.r]).padding(0.28);
  let gMax = 0, sMin = 0;
  allScen.forEach((s) => cats.forEach((c) => {
    gMax = Math.max(gMax, s.emissions[c]); sMin = Math.min(sMin, s.emissions[c]);
  }));
  const eyS = d3.scaleLinear().domain([sMin * 1.05, gMax * 1.08]).range([EH - em.b, em.t]);
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
    const net = s.netEmissions;
    const nv = document.getElementById("netValue");
    nv.textContent = (net >= 0 ? "+" : "−") + Math.abs(net).toFixed(1);
    nv.parentElement.classList.toggle("is-negative", net < 0);
  }

  // ---- OBJECTIVE DECOMPOSITION vs CARBON PRICE ----
  // Three component lines (production cost, resistance to change, social cost of
  // carbon) that sum to the model objective, plus the total. The social cost of
  // carbon (price x net emissions) goes strongly negative once net emissions
  // turn negative, so the y-axis spans negatives with an emphasized zero line.
  const CW = 330, CH = 170, cm = { t: 14, r: 14, b: 30, l: 46 };
  const curveSvg = d3.select("#curveChart").attr("viewBox", `0 0 ${CW} ${CH}`);
  const costKeys = meta.costParts.map((c) => c.key);
  const costColor = {};
  meta.costParts.forEach((c) => { costColor[c.key] = c.color; });
  const xc = d3.scaleLog().domain(d3.extent(allScen, (s) => s.price))
    .range([cm.l, CW - cm.r]);
  let yLo = 0, yHi = 0;
  allScen.forEach((s) => {
    yHi = Math.max(yHi, s.objective, ...costKeys.map((k) => s.costParts[k]));
    yLo = Math.min(yLo, s.objective, ...costKeys.map((k) => s.costParts[k]));
  });
  const yc = d3.scaleLinear().domain([yLo * 1.06, yHi * 1.06]).range([CH - cm.b, cm.t]);
  curveSvg.append("g").attr("class", "axis").attr("transform", `translate(${cm.l},0)`)
    .call(d3.axisLeft(yc).ticks(5).tickFormat((d) => (d / 1000).toFixed(1) + "T"));
  curveSvg.append("g").attr("class", "axis").attr("transform", `translate(0,${CH - cm.b})`)
    .call(d3.axisBottom(xc).tickValues([5, 25, 100, 500]).tickFormat((d) => "$" + d));
  curveSvg.append("text").attr("x", (CW + cm.l) / 2).attr("y", CH - 4)
    .attr("text-anchor", "middle").style("font-size", "9.5px").style("fill", "#9aa8a2")
    .text("Carbon price (USD / t)");
  // Emphasized zero baseline.
  curveSvg.append("line").attr("x1", cm.l).attr("x2", CW - cm.r)
    .attr("y1", yc(0)).attr("y2", yc(0)).attr("stroke", "#9aa8a2");
  const mkLine = (accessor) => d3.line().x((s) => xc(s.price)).y((s) => yc(accessor(s)));
  const compLine = {}, compPath = {};
  costKeys.forEach((k) => {
    compLine[k] = mkLine((s) => s.costParts[k]);
    compPath[k] = curveSvg.append("path").attr("fill", "none")
      .attr("stroke", costColor[k]).attr("stroke-width", 1.6);
  });
  const totalLineGen = mkLine((s) => s.objective);
  const totalPath = curveSvg.append("path").attr("fill", "none")
    .attr("stroke", "#1f2a26").attr("stroke-width", 2.4);
  const costDot = curveSvg.append("circle").attr("r", 5)
    .attr("fill", "#1f2a26").attr("stroke", "#fff").attr("stroke-width", 2);

  // Legend below the chart doubles as the live value readout (sum + parts).
  const costLegendItems = meta.costParts
    .concat([{ key: "objective", label: "Total (objective)", color: "#1f2a26" }]);
  const costLeg = d3.select("#costLegend").selectAll("div.strip-legend__item")
    .data(costLegendItems, (d) => d.key).join("div")
    .attr("class", (d) => "strip-legend__item" + (d.key === "objective" ? " is-animal" : ""));
  costLeg.append("span").attr("class", "strip-legend__swatch").style("background", (d) => d.color);
  costLeg.append("span").attr("class", "strip-legend__label").text((d) => d.label);
  const costLegVals = costLeg.append("span").attr("class", "strip-legend__val");
  const fmtT = (v) => (v < 0 ? "-" : "") + Math.abs(v / 1000).toFixed(2) + "T";

  function drawCostLines() {
    const sc = data.modes[mode].scenarios;
    costKeys.forEach((k) => compPath[k].datum(sc).attr("d", compLine[k]));
    totalPath.datum(sc).attr("d", totalLineGen).raise();
    costDot.raise();
  }
  function updateCost(s) {
    costDot.attr("cx", xc(price)).attr("cy", yc(s.objective));
    costLegVals.text((d) => fmtT(d.key === "objective" ? s.objective : s.costParts[d.key]));
  }

  // ---- generic stacked horizontal strip (diet, feed) ----
  // Every item is annotated in the legend below the strip (swatch + name + live
  // value), so even slivers too thin to hold an in-bar label are still
  // identified. In-bar labels are kept for segments wide enough to fit them, as
  // a quick at-a-glance read of the dominant groups.
  function makeStrip(svgId, legendId, items, valueKeys) {
    // items: [{key,label?,color,animal}]; valueKeys: the scenario fields this
    // strip can display ("diet"/"dietKcal"/"feed"). The active key is passed to
    // the returned updater so the strip can switch units (each key keeps its
    // own full-width scale, from the max total across all scenarios).
    const DW = 1100, DH = 74, dm = { t: 6, r: 10, b: 24, l: 10 };
    const svg = d3.select(svgId).attr("viewBox", `0 0 ${DW} ${DH}`);
    const xByKey = {};
    valueKeys.forEach((vk) => {
      const totalMax = d3.max(allScen, (s) => d3.sum(items, (it) => s[vk][it.key] || 0));
      xByKey[vk] = d3.scaleLinear().domain([0, totalMax || 1]).range([dm.l, DW - dm.r]);
    });
    const gBars = svg.append("g"), gLab = svg.append("g");

    // Static legend (built once); only the value text changes on each render.
    const legItems = d3.select(legendId).selectAll("div.strip-legend__item")
      .data(items, (d) => d.key).join("div")
      .attr("class", (d) => "strip-legend__item" + (d.animal ? " is-animal" : ""));
    legItems.append("span").attr("class", "strip-legend__swatch")
      .style("background", (d) => d.color);
    legItems.append("span").attr("class", "strip-legend__label").text((d) => d.label || d.key);
    const legVals = legItems.append("span").attr("class", "strip-legend__val");
    const fmt = (v) => (v >= 10 ? v.toFixed(0) : v.toFixed(1));

    return (s, valueKey) => {
      const x = xByKey[valueKey];
      let acc = 0;
      const segs = items.map((it) => {
        const v = s[valueKey][it.key] || 0;
        const seg = { ...it, x0: acc, x1: acc + v };
        acc += v;
        return seg;
      });
      gBars.selectAll("rect").data(segs, (d) => d.key).join("rect")
        .attr("x", (d) => x(d.x0)).attr("y", dm.t)
        .attr("width", (d) => Math.max(0, x(d.x1) - x(d.x0)))
        .attr("height", DH - dm.t - dm.b)
        .attr("fill", (d) => d.color).attr("stroke", "#fff").attr("stroke-width", 1);
      gLab.selectAll("text").data(segs.filter((d) => x(d.x1) - x(d.x0) > 42), (d) => d.key)
        .join("text").attr("x", (d) => (x(d.x0) + x(d.x1)) / 2).attr("y", DH - 8)
        .attr("text-anchor", "middle").style("font-size", "11px")
        .style("font-weight", (d) => d.animal ? 700 : 500).style("fill", "#475650")
        .text((d) => d.label || d.key);
      legVals.text((d) => fmt(s[valueKey][d.key] || 0));
    };
  }
  const dietItems = foodGroups.map((g) => ({ key: g.key, label: g.label, color: g.color, animal: g.animal }));
  const feedItems = feedCats.map((f) => ({ key: f.key, label: f.key, color: f.color, animal: false }));
  // The diet strip can display mass (g) or energy (kcal); the feed strip is mass only.
  const hasKcal = allScen.every((s) => s.dietKcal);
  const dietKeys = hasKcal ? ["diet", "dietKcal"] : ["diet"];
  let dietUnit = "g";  // "g" -> diet, "kcal" -> dietKcal
  const dietKey = () => (dietUnit === "kcal" ? "dietKcal" : "diet");
  const updateDiet = makeStrip("#dietChart", "#dietLegend", dietItems, dietKeys);
  const updateFeed = makeStrip("#feedChart", "#feedLegend", feedItems, ["feed"]);

  // ---- logarithmic carbon-price scale ----
  // The slider position p in [0,1] maps to price geometrically (constant ratio
  // per step), matching the roughly geometric spacing of the published sweep.
  // The range is derived from the data and starts at the lowest published
  // price rather than $0, so the slider can never drive an extrapolation below
  // the cheapest scenario. p=0 -> PMIN, p=1 -> PMAX.
  const allPrices = allModes.flatMap((m) => data.modes[m].prices);
  const PMIN = Math.min(...allPrices), PMAX = Math.max(...allPrices);
  const LR = Math.log(PMAX / PMIN);
  const posToPrice = (p) => PMIN * Math.exp(LR * p);
  const priceToPos = (v) => Math.log(v / PMIN) / LR;
  const SLIDER_RES = 1000;

  // ---- ticks (positioned on the log scale) ----
  d3.select("#ticks").selectAll("span")
    .data([5, 10, 25, 50, 100, 200, 500].filter((d) => d >= PMIN && d <= PMAX))
    .join("span")
    .style("left", (d) => `${priceToPos(d) * 100}%`)
    .text((d) => "$" + d);

  // ---- render + wiring ----
  const slider = document.getElementById("slider");
  const priceValue = document.getElementById("priceValue");
  const dietHint = document.getElementById("dietHint");
  function render() {
    const s = interp(mode, price);
    priceValue.textContent = Math.round(price);
    const unitLabel = dietUnit === "kcal" ? "kcal / person / day" : "g / person / day";
    dietHint.textContent = unitLabel + (mode === "fixed"
      ? " - held at the 2020 baseline in this mode"
      : " - plant-based to animal-based");
    updateMap(s); updatePasture(s); updateEmissions(s); updateCost(s);
    updateDiet(s, dietKey()); updateFeed(s, "feed");
  }
  slider.addEventListener("input", (e) => {
    price = posToPrice(+e.target.value / SLIDER_RES); render();
  });

  // Two-state toggles: a click anywhere in the control flips to the other
  // option (so the same spot can be clicked repeatedly to switch back and
  // forth), not only when the inactive button is hit.

  // mode toggle (fixed / flexible); buttons whose mode is absent are disabled
  const syncModeButtons = () =>
    d3.selectAll("#modeToggle .toggle__btn")
      .classed("is-active", function () { return this.dataset.mode === mode; });
  d3.selectAll("#modeToggle .toggle__btn").each(function () {
    if (!allModes.includes(this.dataset.mode)) { this.disabled = true; this.style.opacity = 0.4; }
  });
  syncModeButtons();
  if (allModes.length > 1) {
    document.getElementById("modeToggle").addEventListener("click", () => {
      mode = allModes.find((m) => m !== mode) || mode;
      syncModeButtons();
      drawCostLines(); render();
    });
  }

  // diet unit toggle (g / kcal); disabled if the data lacks calorie figures
  const syncUnitButtons = () =>
    d3.selectAll("#dietUnitToggle .unit-toggle__btn")
      .classed("is-active", function () { return this.dataset.unit === dietUnit; });
  d3.selectAll("#dietUnitToggle .unit-toggle__btn").each(function () {
    if (this.dataset.unit === "kcal" && !hasKcal) { this.disabled = true; this.style.opacity = 0.4; }
  });
  syncUnitButtons();
  if (hasKcal) {
    document.getElementById("dietUnitToggle").addEventListener("click", () => {
      dietUnit = dietUnit === "kcal" ? "g" : "kcal";
      syncUnitButtons();
      render();
    });
  }

  // optional deep-link: ?price=200&mode=flexible&unit=kcal
  const q = new URLSearchParams(location.search);
  price = Math.max(PMIN, Math.min(PMAX, +q.get("price") || PMIN));
  if (allModes.includes(q.get("mode"))) { mode = q.get("mode"); syncModeButtons(); }
  if (q.get("unit") === "kcal" && hasKcal) { dietUnit = "kcal"; syncUnitButtons(); }
  slider.value = Math.round(priceToPos(price) * SLIDER_RES);
  drawCostLines();
  render();
}
