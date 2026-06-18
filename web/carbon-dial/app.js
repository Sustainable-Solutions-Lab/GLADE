// SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
// SPDX-License-Identifier: CC-BY-4.0
//
// Carbon Price Dial - interactive GLADE widget.
// Loads data.json + regions.geojson (produced by export_data.py) and drives a
// synced world map, emissions bar, cost-vs-emissions curve and diet strip from
// a single carbon-price slider, interpolating between solved scenarios.

const DIET_ORDER = [
  "grain", "roots", "vegetables", "fruits", "legumes", "nuts_seeds",
  "oils", "sugar", "dairy", "eggs", "poultry", "fish", "red_meat",
];
const ANIMAL_GROUPS = new Set(["dairy", "eggs", "poultry", "fish", "red_meat"]);
const FG_LABEL = {
  grain: "Grains", roots: "Roots", vegetables: "Vegetables", fruits: "Fruits",
  legumes: "Legumes", nuts_seeds: "Nuts/seeds", oils: "Oils", sugar: "Sugar",
  dairy: "Dairy", eggs: "Eggs", poultry: "Poultry", fish: "Fish",
  red_meat: "Red meat",
};

Promise.all([
  d3.json("data/data.json"),
  d3.json("data/regions.geojson"),
]).then(([data, geo]) => init(data, geo))
  .catch((err) => {
    document.getElementById("dial").insertAdjacentHTML(
      "beforeend",
      `<p style="color:#a6191e">Failed to load data: ${err}</p>`);
  });

function lerp(a, b, t) { return a + (b - a) * t; }

function init(data, geo) {
  const meta = data.meta;
  const scen = data.scenarios;
  const prices = meta.prices;
  const groupColor = meta.mapGroups.map((g) => g.color);

  if (meta.synthetic) {
    const f = document.getElementById("syntheticFlag");
    f.hidden = false;
    document.getElementById("footNote").textContent = "placeholder data";
  }

  // ---- interpolation across scenarios by price ----
  function bracket(price) {
    let i = 0;
    while (i < prices.length - 1 && prices[i + 1] < price) i++;
    const p0 = prices[i];
    const p1 = prices[Math.min(i + 1, prices.length - 1)];
    const t = p1 === p0 ? 0 : (price - p0) / (p1 - p0);
    return { a: scen[i], b: scen[Math.min(i + 1, scen.length - 1)], t };
  }
  function lerpDict(da, db, t) {
    const out = {};
    for (const k of Object.keys(da)) out[k] = lerp(da[k], db[k] ?? da[k], t);
    return out;
  }
  function interp(price) {
    const { a, b, t } = bracket(price);
    const near = t < 0.5 ? a : b; // categorical fields snap to nearest
    const inten = a.regionIntensity.map((v, i) =>
      lerp(v, b.regionIntensity[i], t));
    return {
      emissions: lerpDict(a.emissions, b.emissions, t),
      netEmissions: lerp(a.netEmissions, b.netEmissions, t),
      cost: lerp(a.cost, b.cost, t),
      diet: lerpDict(a.diet, b.diet, t),
      regionGroup: near.regionGroup,
      regionIntensity: inten,
    };
  }

  // ---- MAP ----
  const MW = 720, MH = 360;
  const mapSvg = d3.select("#map").attr("viewBox", `0 0 ${MW} ${MH}`);
  const proj = d3.geoNaturalEarth1().fitExtent([[6, 6], [MW - 6, MH - 6]], geo);
  const path = d3.geoPath(proj);
  mapSvg.append("path") // sphere backdrop
    .attr("d", path({ type: "Sphere" }))
    .attr("fill", "#eef3f1").attr("stroke", "#dfe7e3");
  const regionPaths = mapSvg.append("g").selectAll("path")
    .data(geo.features).join("path")
    .attr("d", path)
    .attr("stroke", "#ffffff").attr("stroke-width", 0.2);

  // legend
  const legend = d3.select("#legend");
  meta.mapGroups.forEach((g) => {
    const item = legend.append("div").attr("class", "legend__item");
    item.append("span").attr("class", "legend__swatch")
      .style("background", g.color);
    item.append("span").text(g.name);
  });

  function updateMap(s) {
    regionPaths.attr("fill", (d, i) => {
      const gi = s.regionGroup[i];
      if (gi < 0) return "#e9eeec";
      return groupColor[gi];
    }).attr("fill-opacity", (d, i) => {
      const gi = s.regionGroup[i];
      if (gi < 0) return 1;
      return 0.12 + 0.88 * s.regionIntensity[i];
    });
  }

  // ---- EMISSIONS BAR ----
  const cats = meta.emissionCategories;
  const EW = 330, EH = 170, em = { t: 12, r: 10, b: 28, l: 38 };
  const emiSvg = d3.select("#emiChart").attr("viewBox", `0 0 ${EW} ${EH}`);
  const exScale = d3.scaleBand().domain(cats)
    .range([em.l, EW - em.r]).padding(0.28);
  let gMax = 0, sMin = 0;
  scen.forEach((s) => cats.forEach((c) => {
    gMax = Math.max(gMax, s.emissions[c]);
    sMin = Math.min(sMin, s.emissions[c]);
  }));
  const eyScale = d3.scaleLinear().domain([sMin * 1.05, gMax * 1.05])
    .range([EH - em.b, em.t]);
  emiSvg.append("g").attr("class", "axis")
    .attr("transform", `translate(${em.l},0)`)
    .call(d3.axisLeft(eyScale).ticks(4).tickSize(-(EW - em.l - em.r)))
    .call((g) => g.select(".domain").remove());
  emiSvg.append("line").attr("x1", em.l).attr("x2", EW - em.r)
    .attr("y1", eyScale(0)).attr("y2", eyScale(0))
    .attr("stroke", "#9aa8a2").attr("stroke-width", 1);
  const SHORT = {
    "Land-use change": "LUC", "Enteric & manure (CH4)": "CH₄",
    "Fertilizer & residues (N2O)": "N₂O", "Sequestration": "Seq.",
  };
  emiSvg.append("g").selectAll("text.lbl").data(cats).join("text")
    .attr("class", "lbl").attr("x", (c) => exScale(c) + exScale.bandwidth() / 2)
    .attr("y", EH - 9).attr("text-anchor", "middle")
    .style("font-size", "10px").style("fill", "#6b7a74")
    .text((c) => SHORT[c] || c);
  const emiBars = emiSvg.append("g").selectAll("rect").data(cats).join("rect")
    .attr("x", (c) => exScale(c)).attr("width", exScale.bandwidth())
    .attr("fill", (c) => c === "Sequestration" ? "#5fa285" : "#c2693f");

  function updateEmissions(s) {
    emiBars.data(cats).attr("y", (c) => Math.min(eyScale(s.emissions[c]), eyScale(0)))
      .attr("height", (c) => Math.abs(eyScale(s.emissions[c]) - eyScale(0)));
    const net = s.netEmissions;
    const nv = document.getElementById("netValue");
    nv.textContent = (net >= 0 ? "+" : "−") + Math.abs(net).toFixed(1);
    nv.parentElement.classList.toggle("is-negative", net < 0);
  }

  // ---- COST vs EMISSIONS CURVE ----
  const CW = 330, CH = 170, cm = { t: 14, r: 14, b: 30, l: 44 };
  const curveSvg = d3.select("#curveChart").attr("viewBox", `0 0 ${CW} ${CH}`);
  const netExtent = d3.extent(scen, (s) => s.netEmissions);
  const costExtent = d3.extent(scen, (s) => s.cost);
  const cx = d3.scaleLinear().domain(netExtent).nice()
    .range([cm.l, CW - cm.r]);
  const cy = d3.scaleLinear().domain(costExtent).nice()
    .range([CH - cm.b, cm.t]);
  curveSvg.append("g").attr("class", "axis")
    .attr("transform", `translate(0,${CH - cm.b})`)
    .call(d3.axisBottom(cx).ticks(4));
  curveSvg.append("g").attr("class", "axis")
    .attr("transform", `translate(${cm.l},0)`)
    .call(d3.axisLeft(cy).ticks(4).tickFormat((d) => (d / 1000).toFixed(1) + "T"));
  curveSvg.append("text").attr("x", (CW + cm.l) / 2).attr("y", CH - 4)
    .attr("text-anchor", "middle").style("font-size", "9.5px")
    .style("fill", "#9aa8a2").text("Net emissions (Gt CO₂-eq)");
  const line = d3.line().x((s) => cx(s.netEmissions)).y((s) => cy(s.cost))
    .curve(d3.curveCatmullRom);
  curveSvg.append("path").datum(scen).attr("d", line)
    .attr("fill", "none").attr("stroke", "#cdd8d3").attr("stroke-width", 2);
  const curveDot = curveSvg.append("circle").attr("r", 5.5)
    .attr("fill", "#3b745f").attr("stroke", "#fff").attr("stroke-width", 2);

  function updateCurve(s) {
    curveDot.attr("cx", cx(s.netEmissions)).attr("cy", cy(s.cost));
  }

  // ---- DIET STRIP ----
  const groups = DIET_ORDER.filter((g) => scen[0].diet[g] !== undefined);
  const fgColor = meta.foodGroupColors;
  const DW = 1100, DH = 78, dm = { t: 8, r: 10, b: 26, l: 10 };
  const dietSvg = d3.select("#dietChart").attr("viewBox", `0 0 ${DW} ${DH}`);
  const totalMax = d3.max(scen, (s) => d3.sum(groups, (g) => s.diet[g]));
  const dx = d3.scaleLinear().domain([0, totalMax]).range([dm.l, DW - dm.r]);
  const dietBars = dietSvg.append("g");
  const dietLabels = dietSvg.append("g");

  function updateDiet(s) {
    let acc = 0;
    const segs = groups.map((g) => {
      const seg = { g, x0: acc, x1: acc + s.diet[g] };
      acc += s.diet[g];
      return seg;
    });
    dietBars.selectAll("rect").data(segs, (d) => d.g).join("rect")
      .attr("x", (d) => dx(d.x0)).attr("y", dm.t)
      .attr("width", (d) => Math.max(0, dx(d.x1) - dx(d.x0)))
      .attr("height", DH - dm.t - dm.b)
      .attr("fill", (d) => fgColor[d.g] || "#bbb")
      .attr("stroke", "#fff").attr("stroke-width", 1);
    dietLabels.selectAll("text").data(segs.filter((d) =>
      dx(d.x1) - dx(d.x0) > 46), (d) => d.g).join("text")
      .attr("x", (d) => (dx(d.x0) + dx(d.x1)) / 2).attr("y", DH - 9)
      .attr("text-anchor", "middle").style("font-size", "11px")
      .style("font-weight", (d) => ANIMAL_GROUPS.has(d.g) ? 700 : 500)
      .style("fill", "#475650").text((d) => FG_LABEL[d.g]);
  }

  // ---- caption ----
  function caption(price) {
    if (price < 1) return "No carbon price: the system minimises monetary cost alone.";
    if (price < 40) return "Low price: the cheapest emission cuts appear - less fertiliser-intensive cropping, modest herd shifts.";
    if (price < 120) return "Moderate price: livestock contracts and croplands begin to be spared for carbon.";
    if (price < 280) return "High price: diets shift towards plants and large areas are reforested.";
    return "Very high price: deep dietary change and large-scale land sparing dominate.";
  }

  // ---- ticks ----
  const tickVals = [0, 100, 200, 300, 400, 500];
  d3.select("#ticks").selectAll("span").data(tickVals).join("span")
    .text((d) => "$" + d);

  // ---- wire up ----
  const slider = document.getElementById("slider");
  const priceValue = document.getElementById("priceValue");
  const captionEl = document.getElementById("caption");
  function render(price) {
    const s = interp(price);
    priceValue.textContent = Math.round(price);
    captionEl.textContent = caption(price);
    updateMap(s);
    updateEmissions(s);
    updateCurve(s);
    updateDiet(s);
  }
  slider.addEventListener("input", (e) => render(+e.target.value));
  // Optional deep-link / embed support: ?price=200
  const startPrice = Math.max(0, Math.min(500,
    +new URLSearchParams(location.search).get("price") || 0));
  slider.value = startPrice;
  render(startPrice);
}
