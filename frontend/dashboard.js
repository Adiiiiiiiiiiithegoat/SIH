(function () {
  const U = window.AppUtils;
  const C = U.COLORS;
  const bbox = window.MOCK_DATA.bbox;
  const STATES = { road: ["passable", "impassable", "unknown"], building: ["damaged", "not_damaged", "unknown"] };
  const el = id => document.getElementById(id);

  const map = L.map("map", { zoomControl: false, attributionControl: false })
    .fitBounds([[bbox.south, bbox.west], [bbox.north, bbox.east]]);
  L.control.zoom({ position: "topright" }).addTo(map);
  L.control.attribution({ prefix: false, position: "bottomright" }).addTo(map);
  map.createPane("areas").style.zIndex = 390;
  map.createPane("detours").style.zIndex = 380;   // under the severed-pocket pane

  // Light gray canvas by default, satellite one click away -- same toggle
  // Google Maps uses. The drawn network and pockets need a white halo once
  // the ground under them is a photo instead of a pale basemap; see
  // .sat-mode in CSS. The canvas ships with no labels, so its place names
  // are a second transparent layer stacked on top -- grouped with the base
  // so the layer control still shows one "Map" choice, not two.
  const B = U.BASEMAPS;
  const baseTiles = L.tileLayer(B.map.url, Object.assign({ attribution: B.map.attribution }, B.map.options));
  const baseLayer = L.layerGroup([
    baseTiles,
    L.tileLayer(B.map.labelsUrl, B.map.options)
  ]).addTo(map);

  // Drop the "Loading map…" cover once the visible tiles are in -- or after a
  // few seconds regardless, so a tile CDN hiccup can't leave it stuck on.
  const hideMapLoading = () => { const n = el("mapLoading"); if (n) n.hidden = true; };
  baseTiles.once("load", hideMapLoading);
  setTimeout(hideMapLoading, 4000);
  const satLayer = L.tileLayer(B.satellite.url, Object.assign({ attribution: B.satellite.attribution }, B.satellite.options));
  // Detour zones are opt-in -- an unchecked overlay so they never compete with
  // the severed pockets, which are the headline.
  const detourR = L.svg({ padding: 0.3, pane: "detours" });
  const detourL = L.layerGroup();
  L.control.layers({ "Map": baseLayer, "Satellite": satLayer },
    { "Detour zones": detourL }, { position: "topright", collapsed: false }).addTo(map);
  map.on("baselayerchange", e => map.getContainer().classList.toggle("sat-mode", e.name === "Satellite"));

  // 6,000+ spans: the base network is canvas or the map will not pan.
  const netR = L.canvas({ padding: 0.3 }).addTo(map);
  const svgR = L.svg({ padding: 0.3 }).addTo(map);
  const areaR = L.svg({ padding: 0.3, pane: "areas" }).addTo(map);
  const netL = L.layerGroup().addTo(map);
  const cutL = L.layerGroup().addTo(map);
  const areaL = L.layerGroup().addTo(map);
  const pinL = L.layerGroup().addTo(map);
  const areaShapes = new Map();
  const cutShapes = new Map();
  const pinShapes = new Map();

  const S = {
    reports: [], roads: [], pockets: [], detours: [],
    filter: "pending", sort: "priority_score", dir: -1,
    report: null, pocket: null,
    placing: false, point: null, marker: null
  };

  /* ------------------------------------------------------------------ map */

  // The legend calls this layer "Reported, open" -- so a closed case must not
  // paint its span. A resolved road is open again and a dismissed one was
  // never shut; either one left highlighted reads as live damage.
  function reportedEdges() {
    const set = new Set();
    S.reports.forEach(r => {
      if (r.asset_type === "road" && r.edge_id && U.isLive(r)) set.add(r.edge_id);
    });
    return set;
  }

  function drawRoads() {
    netL.clearLayers();
    cutL.clearLayers();
    cutShapes.clear();
    const reported = reportedEdges();

    S.roads.forEach(function (road) {
      const cut = road.state === "impassable";
      const unsure = road.state === "unknown";

      if (!cut && !unsure && !reported.has(road.edge_id)) {
        L.polyline(road.coordinates, {
          renderer: netR, interactive: false,
          color: C.neutral, weight: U.roadWeight(road), opacity: 0.85
        }).addTo(netL);
        return;
      }
      const style = cut ? { color: C.bad, weight: 3.5, className: "cut" }
        : unsure ? { color: C.unknown, weight: 2.5, dashArray: "6 5" }
          : { color: C.affected, weight: Math.max(1.6, U.roadWeight(road) + 0.4) };

      const line = L.polyline(road.coordinates, Object.assign({ renderer: svgR }, style))
        .bindTooltip((road.name && road.name !== "<unnamed>" ? road.name : "Unnamed span") +
          " · " + U.stateLabel(road.state))
        .on("click", e => { if (S.placing) map.fire("click", e); })
        .addTo(cutL);
      if (cut) cutShapes.set(road.edge_id, line);
    });
  }

  function drawAreas() {
    areaL.clearLayers();
    areaShapes.clear();
    S.pockets.forEach(function (p) {
      const edges = U.severingEdges(p);
      const cause = edges.length === 1
        ? "Blocked by " + (edges[0].name && edges[0].name !== "<unnamed>" ? edges[0].name : "an unnamed span")
        : edges.length + " blocked roads cut this area off";

      const shape = L.polygon(p.polygon, { renderer: areaR, pane: "areas", className: "pocket" })
        .on("click", function (e) {
          if (S.placing) return map.fire("click", e);
          L.DomEvent.stop(e);
          openPocket(p.id);
        })
        .addTo(areaL);

      // A permanent, interactive tooltip is Leaflet's own clickable label. A
      // divIcon marker here sits under the polygon and never gets the click.
      shape.bindTooltip(
        '<span class="tag-n">' + U.escape(U.populationText(p)) + "</span> cut off" +
        '<span class="tag-why">Why?</span>' +
        '<span class="tag-cause">' + U.escape(cause) + "</span>",
        { permanent: true, interactive: true, direction: "center", className: "area-tag", opacity: 1 });

      areaShapes.set(p.id, shape);
      const path = shape._path;
      if (!path) return;
      path.setAttribute("tabindex", "0");
      path.setAttribute("role", "button");
      path.setAttribute("aria-label", "Area cut off, " + U.populationText(p) +
        " people affected. " + cause + ". Open for the roads responsible.");
      L.DomEvent.on(path, "keydown", function (e) {
        if (e.key !== "Enter" && e.key !== " ") return;
        L.DomEvent.stop(e);
        openPocket(p.id);
      });
    });
  }

  function drawPins() {
    pinL.clearLayers();
    pinShapes.clear();
    S.reports.forEach(function (r) {
      if (r.status === "rejected") return;
      // A resolved case keeps its pin -- the report happened -- but not the
      // red of a road that is still cut.
      const colour = U.isLive(r) ? U.markerColor(r.state) : C.neutral;
      const marker = L.marker([r.lat, r.lon], {
        icon: L.divIcon({
          className: "",
          html: '<div class="pin ' + r.asset_type + '" style="background:' + colour + ';color:' + colour + '"></div>',
          iconSize: [11, 11], iconAnchor: [6, 6]
        })
      })
        .bindTooltip(U.label("asset_type", r.asset_type) + " · " + U.stateLabel(r.state) +
          (U.isLive(r) ? "" : " · " + U.label("status_short", r.status)))
        .on("click", () => openReport(r.id))
        .addTo(pinL);
      pinShapes.set(r.id, marker);
    });
  }

  function drawDetours() {
    detourL.clearLayers();
    (S.detours || []).forEach(function (d) {
      if (!d.polygon || d.polygon.length < 3) return;
      const people = U.populationText({ population: d.population, population_source: d.population_source });
      L.polygon(d.polygon, { renderer: detourR, pane: "detours", className: "detour" })
        .bindTooltip("+" + Number(d.added_km || 0).toFixed(1) + " km detour · " + people + " people",
          { sticky: true })
        .addTo(detourL);
    });
  }

  // A one-shot highlight (CSS transition, not a looping animation -- this
  // product's design rule is no keyframes) so a report or pocket that just
  // appeared between refreshes catches the eye without redrawing anything.
  function flashOnce(node) {
    if (!node) return;
    node.classList.add("new");
    setTimeout(() => node.classList.remove("new"), 3000);
  }
  function flashChanges(prevPocketIds, prevReportIds) {
    if (prevPocketIds) S.pockets.forEach(function (p) {
      if (prevPocketIds.has(p.id)) return;
      const shape = areaShapes.get(p.id);
      flashOnce(shape && shape._path);
    });
    if (prevReportIds) S.reports.forEach(function (r) {
      if (prevReportIds.has(r.id)) return;
      const marker = pinShapes.get(r.id);
      const wrap = marker && marker.getElement && marker.getElement();
      flashOnce(wrap && wrap.querySelector(".pin"));
    });
  }

  // Emphasise the spans responsible for a cut-off area.
  function highlight(pocket) {
    cutShapes.forEach(line => line._path && line._path.classList.remove("on"));
    areaShapes.forEach(function (shape) {
      if (shape._path) shape._path.classList.remove("on");
      const t = shape.getTooltip && shape.getTooltip();
      if (t && t._container) t._container.classList.remove("on");
    });
    if (!pocket) return;
    const shape = areaShapes.get(pocket.id);
    if (shape && shape._path) shape._path.classList.add("on");
    const tip = shape && shape.getTooltip && shape.getTooltip();
    if (tip && tip._container) tip._container.classList.add("on");
    U.severingEdgeIds(pocket).forEach(function (id) {
      const line = cutShapes.get(id);
      if (line && line._path) line._path.classList.add("on");
    });
  }

  /* --------------------------------------------------------------- figures */

  // A figure only earns its place in the bar when it has something to say --
  // no bridges down means no "0 bridges" clutter next to real numbers.
  function drawOptionalFigure(wrapId, valueId, count) {
    el(wrapId).hidden = count === 0;
    if (count > 0) el(valueId).textContent = count.toLocaleString();
  }

  function drawImpact() {
    const s = U.impactSummary(S.reports, S.pockets, S.roads);
    el("figRoads").textContent = s.roadsImpassable.toLocaleString();
    el("figAreas").textContent = s.areasSevered.toLocaleString();
    el("figPeople").textContent = U.peopleText(s);
    el("figWorking").textContent = S.reports.filter(r => r.status === "in_progress").length.toLocaleString();
    el("impactNote").textContent = U.populationCaveat(s);
    drawOptionalFigure("figBridgesWrap", "figBridges", s.bridgesImpassable);
    drawOptionalFigure("figBuildingsWrap", "figBuildings", s.buildingsDamaged);
  }

  /* ----------------------------------------------------------------- queue */

  // Three lanes: waiting on you, being worked, done with.
  function inLane(r, lane) {
    if (lane === "closed") return r.status === "resolved" || r.status === "rejected";
    return r.status === lane;
  }
  function visible() {
    return S.reports.filter(r => inLane(r, S.filter));
  }

  function sorted(list) {
    const key = S.sort;
    return list.slice().sort(function (a, b) {
      const x = a[key], y = b[key];
      if (x == null && y == null) return 0;
      if (x == null) return 1;            // unranked and unknown always sort last
      if (y == null) return -1;
      if (typeof x === "number") return (x - y) * S.dir;
      return String(x).localeCompare(String(y)) * S.dir;
    });
  }

  const DOT = { bad: "bad", unknown: "warn", good: "ok" };

  function cell(r) {
    const band = U.priorityBand(r.priority_score);
    const dot = DOT[U.stateClass(r.state)] || "";
    const why = U.escape(r.priority_reason || "—");
    return '<tr data-id="' + r.id + '" class="band-' + band.key + '"' +
      (S.report === r.id ? ' aria-selected="true"' : "") + '>' +
      '<td class="num muted">#' + r.id + '</td>' +
      '<td class="right"><span class="band band-' + band.key + '"><em>' + band.label + '</em>' +
      (r.priority_score ? '<span class="v">' + Math.round(r.priority_score).toLocaleString() + '</span>' : "") +
      '</span></td>' +
      '<td>' + U.label("asset_type", r.asset_type) + '</td>' +
      '<td><span class="dot ' + dot + (r.asset_type === "building" ? " building" : "") + '"></span>' +
      U.stateLabel(r.state) + '</td>' +
      '<td class="right num">' + r.n_reports + '</td>' +
      '<td>' + U.label("detection_mode", r.detection_mode) + '</td>' +
      '<td class="muted">' + U.escape(U.timeAgo(r.created_at)) + '</td>' +
      '<td class="txt-' + U.statusClass(r.status) + '">' + U.label("status_short", r.status) + '</td>' +
      '<td class="why" title="' + why + '">' + why + '</td></tr>';
  }

  function drawQueue() {
    el("nPending").textContent = S.reports.filter(r => inLane(r, "pending")).length;
    el("nProgress").textContent = S.reports.filter(r => inLane(r, "in_progress")).length;
    el("nClosed").textContent = S.reports.filter(r => inLane(r, "closed")).length;

    const list = sorted(visible());
    const body = el("rows");
    if (!list.length) {
      body.innerHTML = '<tr><td class="empty" colspan="9">Nothing in this queue.</td></tr>';
      return;
    }
    let split = false;
    body.innerHTML = list.map(function (r) {
      let head = "";
      if (S.sort === "priority_score" && r.priority_score == null && !split) {
        split = true;
        head = '<tr class="divider"><td colspan="9">Not yet ranked</td></tr>';
      }
      return head + cell(r);
    }).join("");

    body.querySelectorAll("tr[data-id]").forEach(row =>
      row.addEventListener("click", () => openReport(Number(row.dataset.id))));

    document.querySelectorAll("thead th[data-sort]").forEach(function (th) {
      const active = th.dataset.sort === S.sort;
      if (active) th.setAttribute("aria-sort", S.dir === -1 ? "descending" : "ascending");
      else th.removeAttribute("aria-sort");
      const mark = th.querySelector(".sort");
      if (mark) mark.remove();
      if (active) th.querySelector("button").insertAdjacentHTML("beforeend",
        '<span class="sort">' + (S.dir === -1 ? "▾" : "▴") + "</span>");
    });
  }

  /* ---------------------------------------------------------------- detail */

  const detail = el("detail");

  /* Full-screen field photo. Click a report thumbnail to open; click the photo
     to toggle actual-pixels zoom; click the backdrop or press Esc to close. */
  const lightbox = el("lightbox");
  const lightboxImg = el("lightboxImg");

  function openLightbox(src) {
    lightboxImg.src = src;
    lightbox.classList.remove("zoomed");
    lightbox.classList.add("open");
    lightbox.setAttribute("aria-hidden", "false");
  }

  function closeLightbox() {
    lightbox.classList.remove("open", "zoomed");
    lightbox.setAttribute("aria-hidden", "true");
    lightboxImg.removeAttribute("src");
  }

  detail.addEventListener("click", function (e) {
    const img = e.target.closest("img.zoomable");
    if (img) openLightbox(img.src);
  });
  lightbox.addEventListener("click", function (e) {
    if (e.target === lightboxImg) lightbox.classList.toggle("zoomed");
    else closeLightbox();
  });

  function openDetail(title, html) {
    detail.innerHTML = '<div class="detail-bar"><h2>' + title + '</h2>' +
      '<button type="button" class="btn close" id="closeDetail">Close</button></div>' +
      '<div class="detail-body">' + html + '</div>';
    detail.classList.add("open");
    detail.setAttribute("aria-hidden", "false");
    el("closeDetail").addEventListener("click", closeDetail);
  }

  function closeDetail() {
    detail.classList.remove("open");
    detail.setAttribute("aria-hidden", "true");
    detail.innerHTML = "";
    S.report = null;
    S.pocket = null;
    highlight(null);
    cancelPlacing();
    drawQueue();
  }

  const dd = (k, v) => "<dt>" + k + "</dt><dd>" + v + "</dd>";

  // Which cut-off areas this report's span is responsible for.
  function areasCausedBy(report) {
    if (!report.edge_id) return [];
    return S.pockets.filter(p => U.severingEdgeIds(p).indexOf(report.edge_id) !== -1);
  }

  function roadNameSuffix(edgeId) {
    const road = S.roads.find(rd => rd.edge_id === edgeId);
    return road && road.name && road.name !== "<unnamed>" ? " — " + U.escape(road.name) : "";
  }

  function openReport(id, fromPocket) {
    const r = S.reports.find(x => x.id === id);
    if (!r) return;
    S.report = id;
    S.pocket = fromPocket || null;
    if (!fromPocket) highlight(null);
    drawQueue();
    map.setView([r.lat, r.lon], Math.max(map.getZoom(), 15));

    const band = U.priorityBand(r.priority_score);
    const causes = areasCausedBy(r);
    const moves = U.nextStatuses(r.status);
    const stateOpts = STATES[r.asset_type] || [];

    openDetail("Report #" + r.id + " · " + U.label("asset_type", r.asset_type) + " · " + U.stateLabel(r.state),
      '<div class="cols">' +
      (r.image_path ? '<img class="zoomable" src="' + U.escape(r.image_path) + '" alt="Field photo for report ' + r.id + '" title="Click to view full screen">' : '<div class="note">No photo attached.</div>') +

      '<div><dl>' +
      dd("State", '<span class="txt-' + (DOT[U.stateClass(r.state)] || "neutral") + '">' + U.stateLabel(r.state) + "</span>") +
      dd("Status", '<span class="txt-' + U.statusClass(r.status) + '">' + U.label("status", r.status) + "</span>") +
      dd("Priority", '<span class="band band-' + band.key + '" style="justify-content:flex-start"><em>' + band.label + "</em>" +
        (r.priority_score ? '<span class="v">' + Math.round(r.priority_score).toLocaleString() + "</span>" : "") + "</span>") +
      dd("Reported", U.escape(U.timeAgo(r.created_at)) +
        ' <span class="aside num">' + U.escape(String(r.created_at || "").slice(0, 16).replace("T", " ")) + "</span>") +
      dd("Confidence", '<span class="num">' + Math.round(r.confidence * 100) + "%</span>" +
        (r.detection_mode === "manual" ? ' <span class="aside">operator asserted</span>' : "")) +
      dd("Source", U.label("detection_mode", r.detection_mode)) +
      dd("Corroboration", r.n_reports > 1 ? r.n_reports + " reports on this span" : "Single report") +
      dd("Location", '<span class="num">' + r.lat.toFixed(5) + ", " + r.lon.toFixed(5) + "</span>" +
        (r.gps_accuracy_m == null ? ' <span class="aside">accuracy unrecorded</span>'
          : ' <span class="aside">±' + r.gps_accuracy_m + " m</span>")) +
      dd("Road span", r.edge_id ? '<span class="num">' + U.escape(r.edge_id) + "</span>" : "Not bound to a road") +
      "</dl></div>" +

      "<div>" +
      '<div class="block"><h3>Assessment</h3><div>' +
      U.escape(r.priority_reason || "No assessment recorded.") + "</div></div>" +

      (causes.length
        ? '<div class="block"><h3>Consequence</h3><ul class="rows">' + causes.map(p =>
            '<li><button type="button" class="line" data-area="' + U.escape(p.id) + '">' +
            '<span class="grow">Cuts off ' + U.escape(U.populationText(p)) + " people from " +
            U.escape(p.lost_facility || "care") + "</span>" +
            '<span class="aside">see area →</span></button></li>').join("") + "</ul></div>"
        : "") +

      (r.state === "unknown"
        ? '<div class="block"><h3>Classify</h3>' +
          '<p class="note">The detector could not tell. If the photo can, set it here — that is an operator assertion, so it is recorded as manual at full confidence.</p>'
        : '<div class="block"><h3>Correct the state if wrong</h3>') +
      '<div class="inline">' + stateOpts.filter(v => v !== r.state).map(v =>
        '<button type="button" class="btn" data-state="' + v + '">Mark ' +
        U.stateLabel(v).toLowerCase() + "</button>").join("") + "</div></div>" +

      (r.asset_type === "road"
        ? '<div class="block"><h3>Road binding</h3>' +
          '<p class="note">Bound to ' +
          (r.edge_id ? '<span class="num">' + U.escape(r.edge_id) + "</span>" + roadNameSuffix(r.edge_id)
            : "no road span") +
          ". Wrong span? Re-bind it to a nearby candidate.</p>" +
          '<div class="inline"><button type="button" class="btn" id="rebindBtn">Change binding</button></div>' +
          '<div id="rebindArea"></div></div>'
        : "") +

      '<div class="block"><h3>Workflow</h3><div class="inline">' +
      (moves.length
        ? moves.map((next, i) =>
            '<button type="button" class="btn ' +
            (next === "rejected" ? "btn-danger" : i === 0 ? "btn-primary" : "") +
            '" data-act="' + next + '">' + U.escape(U.actionLabel(r.status, next)) + "</button>").join("")
        : '<span class="note">No moves available.</span>') +
      "</div></div>" +

      (fromPocket ? '<div class="block"><button type="button" class="btn" id="backArea">← Back to cut-off area</button></div>' : "") +
      "</div></div>");

    detail.querySelectorAll("[data-act]").forEach(b =>
      b.addEventListener("click", () => U.busy(b, () => move(r.id, b.dataset.act))));
    detail.querySelectorAll("[data-state]").forEach(b =>
      b.addEventListener("click", () => U.busy(b, () => reclassify(r.id, b.dataset.state))));
    detail.querySelectorAll("[data-area]").forEach(b =>
      b.addEventListener("click", () => openPocket(b.dataset.area)));
    const back = el("backArea");
    if (back) back.addEventListener("click", () => openPocket(fromPocket));
    const rebindBtn = el("rebindBtn");
    if (rebindBtn) rebindBtn.addEventListener("click", () => U.busy(rebindBtn, () => openRebind(r)));
  }

  // Lazily fetch the candidate shortlist (only GET /api/reports/{id} carries
  // it) and let the operator re-point a mis-bound photo at a nearby span.
  async function openRebind(r) {
    const full = await API.getReport(r.id);
    const roadsById = {};
    S.roads.forEach(rd => { roadsById[rd.edge_id] = rd; });
    const opts = U.bindOptions(full, roadsById);
    const area = el("rebindArea");
    if (!area) return;
    if (!opts.length) {
      area.innerHTML = '<p class="note">No candidate spans recorded for this report.</p>';
      return;
    }
    area.innerHTML = '<div class="field"><label class="label" for="rebindSel">Bind to</label>' +
      '<select id="rebindSel">' + opts.map(o =>
        '<option value="' + U.escape(o.edge_id) + '"' + (o.current ? " selected" : "") + ">" +
        U.escape(o.label) + "</option>").join("") + "</select></div>" +
      '<div class="inline"><button type="button" class="btn btn-primary" id="rebindApply">Apply binding</button></div>';
    el("rebindApply").addEventListener("click", () => U.busy(el("rebindApply"), async function () {
      const val = el("rebindSel").value;
      if (val && val !== full.edge_id) {
        await API.updateEdge(r.id, val);
        await load();
      }
      openReport(r.id, S.pocket);
    }));
  }

  function openPocket(id) {
    const p = S.pockets.find(x => x.id === id);
    if (!p) return;
    S.pocket = id;
    S.report = null;
    drawQueue();
    highlight(p);
    if (p.polygon && p.polygon.length) map.fitBounds(L.latLngBounds(p.polygon), { padding: [40, 40], maxZoom: 15 });

    const known = p.population != null && p.population_source !== "missing";
    const edges = U.severingEdges(p);
    const byId = {};
    S.reports.forEach(r => { byId[r.id] = r; });
    const ids = U.pocketReportIds(p);
    const inside = p.population_settlements || [];

    openDetail("Area cut off · " + (p.lost_facility ? "lost " + U.escape(p.lost_facility) : "no facility in reach"),
      '<div class="cols">' +
      '<div><dl>' +
      dd("People affected", known
        ? '<span class="num">' + U.populationText(p) + "</span>"
        : '<span class="txt-warn">Unknown</span>') +
      dd("Source", U.label("population_source", p.population_source)) +
      (p.population_coverage && p.population_coverage !== "full"
        ? dd("Coverage", '<span class="txt-warn">' + U.label("population_coverage", p.population_coverage) + "</span>") : "") +
      dd("Lost access to", U.escape(p.lost_facility || "No facility in reach")) +
      dd("Prior access", '<span class="num">' + U.kmText(p.prior_access_km) + "</span>") +
      dd("Area", '<span class="num">' + U.areaText(p.hull_area_km2) + "</span>") +
      dd("Junctions", '<span class="num">' + (p.n_nodes == null ? "—" : p.n_nodes.toLocaleString()) + "</span>") +
      '</dl>' +
      (known ? "" : '<p class="note">Not measured — not zero. The raster holds no valid count here.</p>') +
      '</div>' +

      '<div class="block"><h3>Roads causing the cut' + (edges.length > 1 ? " (" + edges.length + ")" : "") + '</h3>' +
      (edges.length
        ? '<ul class="rows">' + edges.map(e =>
            '<li><div class="line"><span class="grow">' +
            U.escape(e.name && e.name !== "<unnamed>" ? e.name : "Unnamed span") +
            (e.bridge ? ' <span class="txt-bad">· bridge</span>' : "") +
            '</span><span class="aside">' + U.escape(U.highwayLabel(e.highway)) + " · " +
            U.metresText(e.length_m) + "</span></div></li>").join("") + "</ul>" +
          (edges.length > 1 ? '<p class="note">Clearing any one of these may not reconnect the area.</p>' : "")
        : '<p class="note">No severing span recorded.</p>') +
      '</div>' +

      '<div><div class="block"><h3>Reports behind the blockage</h3>' +
      (ids.length
        ? '<ul class="rows">' + ids.map(function (rid) {
            const r = byId[rid];
            return '<li><button type="button" class="line" data-report="' + rid + '">' +
              '<span class="grow">Report #' + rid + (r ? " · " + U.stateLabel(r.state) : "") + "</span>" +
              '<span class="aside">' + (r ? U.label("status_short", r.status) : "not loaded") + " →</span></button></li>";
          }).join("") + "</ul>"
        : '<p class="note">No live report attached.</p>') +
      '</div>' +
      '<div class="block"><h3>Nearest named place</h3>' +
      (p.nearest_place
        ? "<div>" + U.escape(p.nearest_place) + ' <span class="aside num">' + U.kmText(p.nearest_place_km) + "</span></div>" +
          '<p class="note">A map label for orientation only — not necessarily inside this area, and it did not decide the severance.</p>'
        : '<p class="note">No named place nearby.</p>') +
      (inside.length
        ? '<h3 style="margin-top:var(--s3)">Settlements inside</h3><div>' + inside.map(U.escape).join(", ") + "</div>"
        : "") +
      '</div></div></div>');

    detail.querySelectorAll("[data-report]").forEach(b =>
      b.addEventListener("click", () => openReport(Number(b.dataset.report), id)));
  }

  // The live scoring constants (GET/POST /api/settings). An escape hatch for
  // an operator who needs to retune in the field -- plain number inputs, no
  // per-key help; whoever opens this knows what the weights mean.
  async function openSettings() {
    const s = await API.getSettings();
    const keys = Object.keys(s);
    if (!keys.length) {
      openDetail("Priority tuning", '<p class="note">No adjustable settings reported by the server.</p>');
      return;
    }
    openDetail("Priority tuning",
      '<p class="note">Live scoring constants. Saving applies them and re-ranks the whole queue. ' +
      'These came out of a parameter sweep — change them only with a reason.</p>' +
      '<div class="settings-grid">' + keys.map(k =>
        '<div class="field"><label class="label" for="set-' + k + '">' + U.escape(k.replace(/_/g, " ")) + "</label>" +
        '<input type="number" step="any" id="set-' + k + '" value="' + U.escape(String(s[k])) + '"></div>').join("") +
      "</div>" +
      '<div class="inline"><button type="button" class="btn btn-primary" id="setSave">Save &amp; re-rank</button>' +
      '<button type="button" class="btn" id="setReload">Discard changes</button></div>' +
      '<div class="err" id="setErr" role="alert"></div>');
    el("setReload").addEventListener("click", openSettings);
    el("setSave").addEventListener("click", () => U.busy(el("setSave"), async function () {
      const body = {};
      let bad = null;
      keys.forEach(function (k) {
        const v = Number(el("set-" + k).value);
        if (!Number.isFinite(v)) bad = k;
        body[k] = v;
      });
      if (bad) return fail(el("setErr"), U.escape(bad.replace(/_/g, " ")) + " must be a number.");
      await API.setSettings(body);
      closeDetail();
      await load();
    }));
  }

  async function move(id, status) {
    await API.updateStatus(id, status);
    S.filter = status === "resolved" || status === "rejected" ? "closed" : status;
    syncFilters();
    await load();
    openReport(id, S.pocket);
  }

  async function reclassify(id, state) {
    await API.updateState(id, state);
    await load();
    openReport(id, S.pocket);
  }

  /* ------------------------------------------------------- manual reporter */

  function stateOptions(asset) {
    return STATES[asset].map(v => '<option value="' + v + '">' + U.stateLabel(v) + "</option>").join("");
  }

  function cancelPlacing() {
    S.placing = false;
    el("mapwrap").classList.remove("placing");
    el("hint").classList.remove("on");
    if (S.marker) { map.removeLayer(S.marker); S.marker = null; }
    S.point = null;
  }

  el("addBtn").addEventListener("click", function () {
    closeDetail();
    S.placing = true;
    el("mapwrap").classList.add("placing");
    el("hint").classList.add("on");
  });

  el("tuneBtn").addEventListener("click", () => U.busy(el("tuneBtn"), openSettings));

  map.on("click", function (e) {
    if (!S.placing) return;
    S.point = e.latlng;
    if (S.marker) map.removeLayer(S.marker);
    S.marker = L.marker(e.latlng, {
      icon: L.divIcon({
        className: "",
        html: '<div class="pin road" style="background:' + C.unknown + ';color:' + C.unknown + '"></div>',
        iconSize: [11, 11], iconAnchor: [6, 6]
      })
    }).addTo(map);
    el("hint").classList.remove("on");
    el("mapwrap").classList.remove("placing");
    openForm();
  });

  function openForm() {
    openDetail("New report · " + S.point.lat.toFixed(5) + ", " + S.point.lng.toFixed(5),
      '<div class="cols"><div>' +
      '<div class="field"><label class="label" for="fAsset">What is affected</label>' +
      '<select id="fAsset"><option value="road">Road</option><option value="building">Building</option></select></div>' +
      '<div class="field"><label class="label" for="fState">Observed state</label>' +
      '<select id="fState">' + stateOptions("road") + "</select></div>" +
      '<div class="err" id="fErr" role="alert"></div>' +
      '<div class="actions"><button type="button" class="btn btn-primary" id="fSave">File report</button>' +
      '<button type="button" class="btn" id="fCancel">Cancel</button></div>' +
      '</div><div class="note">The report is bound to the nearest road span and scored against the network before it enters the queue.</div></div>');

    const asset = el("fAsset"), obs = el("fState"), err = el("fErr");
    asset.addEventListener("change", () => { obs.innerHTML = stateOptions(asset.value); });
    el("fCancel").addEventListener("click", closeDetail);
    el("fSave").addEventListener("click", function () {
      if (!S.point) return fail(err, "Place a point on the map first.");
      if (!STATES[asset.value]) return fail(err, "Choose road or building.");
      if (STATES[asset.value].indexOf(obs.value) === -1) {
        return fail(err, U.stateLabel(obs.value) + " is not a valid state for a " + asset.value + ".");
      }
      err.classList.remove("on");
      U.busy(el("fSave"), async function () {
        await API.createReport({
          lat: S.point.lat, lon: S.point.lng,
          asset_type: asset.value, state: obs.value, image_path: "mocks/images/report-6.svg"
        });
        cancelPlacing();
        closeDetail();
        S.filter = "pending";
        syncFilters();
        await load();
      });
    });
  }

  function fail(node, message) { node.textContent = message; node.classList.add("on"); }

  /* ---------------------------------------------------------------- chrome */

  function syncFilters() {
    document.querySelectorAll(".filter").forEach(f =>
      f.setAttribute("aria-selected", String(f.dataset.status === S.filter)));
  }

  document.querySelectorAll(".filter").forEach(f => f.addEventListener("click", function () {
    S.filter = f.dataset.status;
    syncFilters();
    drawQueue();
  }));

  document.querySelectorAll("thead th[data-sort]").forEach(th =>
    th.querySelector("button").addEventListener("click", function () {
      const key = th.dataset.sort;
      // Numbers open descending (worst first); text opens ascending.
      if (S.sort === key) S.dir = -S.dir;
      else { S.sort = key; S.dir = ["asset_type", "state", "detection_mode", "priority_reason", "status"].indexOf(key) === -1 ? -1 : 1; }
      drawQueue();
    }));

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (lightbox.classList.contains("open")) closeLightbox();
    else if (S.placing || detail.classList.contains("open")) closeDetail();
  });

  let lastSyncedAt = null, syncOk = true;

  function setSync(text, kind) {
    const node = el("syncStatus");
    if (!node) return;
    node.textContent = text;
    node.className = "sync-status" + (kind ? " " + kind : "");
  }

  function tick() {
    const now = new Date();
    el("clock").textContent = "Surathkal · " +
      now.toLocaleDateString(undefined, { day: "2-digit", month: "short" }) + " " +
      now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    // Only the clock ticks during an outage -- overwriting the warning with a
    // freshly stale "Synced Xs ago" would hide the one thing worth flagging.
    if (syncOk && lastSyncedAt) setSync("Synced " + U.timeAgo(new Date(lastSyncedAt).toISOString()));
  }

  /* ------------------------------------------------------------------ load */

  let loading = false, knownPocketIds = null, knownReportIds = null;

  async function load() {
    // A 20 s auto-refresh and a manual action's own load() can land close
    // together; both would fetch the same authoritative state, so the second
    // is just wasted network, not a correctness risk -- skip it rather than
    // let two in-flight loads redraw over each other.
    if (loading) return;
    loading = true;
    setSync("Syncing…");
    try {
      const [reports, roads, pockets, detours] = await Promise.all([
        API.getReports(), API.getRoads(), API.getPockets(),
        API.getDetours().catch(() => [])]);
      S.reports = reports;
      S.roads = roads;
      S.pockets = pockets;
      S.detours = detours;
      drawRoads();
      drawAreas();
      drawDetours();
      drawPins();
      drawQueue();
      drawImpact();
      if (S.pocket) {
        const open = S.pockets.find(p => p.id === S.pocket);
        if (open) highlight(open); else closeDetail();
      }
      flashChanges(knownPocketIds, knownReportIds);
      knownPocketIds = new Set(S.pockets.map(p => p.id));
      knownReportIds = new Set(S.reports.map(r => r.id));
      syncOk = true;
      lastSyncedAt = Date.now();
      setSync("Synced just now");
    } catch (error) {
      syncOk = false;
      setSync("Could not reach the server — retrying", "warn");
    } finally {
      loading = false;
    }
  }

  tick();
  setInterval(tick, 30000);
  // A queue meant to reflect live conditions should not need a manual
  // refresh to notice a new report or a cleared road -- this is what makes
  // "operations console" mean something rather than "static snapshot".
  setInterval(load, 20000);
  syncFilters();
  load();
})();
