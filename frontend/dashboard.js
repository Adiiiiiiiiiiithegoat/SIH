(function () {
  const bbox = window.MOCK_DATA.bbox;
  const map = L.map("map").fitBounds([[bbox.south, bbox.west], [bbox.north, bbox.east]]);
  const roadLayer = L.layerGroup().addTo(map), pocketLayer = L.layerGroup().addTo(map), pinLayer = L.layerGroup().addTo(map);
  const state = { reports: [], filter: "pending", selected: null, manual: false, mode: "model" };
  const list = document.getElementById("reportList"), detail = document.getElementById("detail");
  const color = AppUtils.markerColor;
  function roadStyle(road) { return { color: color(road.state), weight: road.state === "impassable" ? 7 : 4, opacity: .9, dashArray: road.state === "unknown" ? "10 8" : null }; }
  function renderMap(roads, pockets) {
    roads.forEach(road => L.polyline(road.coordinates, roadStyle(road)).bindTooltip(road.edge_id).addTo(roadLayer));
    pockets.forEach(pocket => L.polygon(pocket.polygon, { color: "#6b7477", weight: 1, fillColor: "#899294", fillOpacity: .28, dashArray: "3 5" }).bindTooltip((pocket.lost_facility || "Severed pocket") + " · " + AppUtils.populationText(pocket)).addTo(pocketLayer));
  }
  function markerFor(report) {
    const icon = L.divIcon({ className: "", html: '<div class="report-marker ' + report.asset_type + '" style="background:' + color(report.state) + '"><span></span></div>', iconSize: [22, 22], iconAnchor: [11, 11] });
    return L.marker([report.lat, report.lon], { icon }).on("click", () => select(report.id));
  }
  function refreshPins() { pinLayer.clearLayers(); state.reports.forEach(report => markerFor(report).addTo(pinLayer)); }
  function renderList() {
    const visible = AppUtils.sortReports(state.reports.filter(report => state.filter === "closed" ? report.status !== "pending" : report.status === "pending"));
    document.getElementById("mapCount").textContent = state.reports.length + " reports · " + visible.length + " " + state.filter;
    document.getElementById("pendingCount").textContent = state.reports.filter(r => r.status === "pending").length;
    document.getElementById("closedCount").textContent = state.reports.filter(r => r.status !== "pending").length;
    list.innerHTML = visible.length ? visible.map(report => '<article class="report-row" id="report-' + report.id + '" data-id="' + report.id + '"><img class="thumb" src="' + report.image_path + '" alt=""><div><div class="row-top"><span class="type">' + report.asset_type + ' · ' + AppUtils.stateLabel(report.state) + '</span><span class="badge ' + report.status + '">' + report.status + '</span></div><div class="reason">' + AppUtils.escape(report.priority_reason || "No priority reason provided") + '</div><div class="meta"><span>' + Math.round(report.confidence * 100) + '% confidence</span><span>' + (report.n_reports > 1 ? report.n_reports + " corroborating reports" : "Single report") + '</span><span class="mode ' + report.detection_mode + '">' + report.detection_mode + '</span></div></div></article>').join("") : '<div class="empty">Nothing in this queue.</div>';
    list.querySelectorAll(".report-row").forEach(row => row.addEventListener("click", () => select(Number(row.dataset.id))));
  }
  function select(id) {
    const report = state.reports.find(item => item.id === id); if (!report) return;
    state.selected = id;
    list.querySelectorAll(".report-row").forEach(row => row.classList.toggle("selected", Number(row.dataset.id) === id));
    const row = document.getElementById("report-" + id); if (row) row.scrollIntoView({ block: "nearest" });
    map.setView([report.lat, report.lon], Math.max(map.getZoom(), 14));
    detail.innerHTML = '<img src="' + report.image_path + '" alt="Report ' + report.id + '"><div class="detail-body"><div class="eyebrow">Reference #' + report.id + '</div><h2>' + report.asset_type + ' · ' + AppUtils.stateLabel(report.state) + '</h2><div class="field-grid"><div class="kv"><small>Confidence</small><strong>' + Math.round(report.confidence * 100) + '%</strong></div><div class="kv"><small>Detection</small><strong>' + report.detection_mode + '</strong></div><div class="kv"><small>GPS accuracy</small><strong>' + (report.gps_accuracy_m == null ? "unknown" : report.gps_accuracy_m + " m") + '</strong></div><div class="kv"><small>Priority score</small><strong>' + (report.priority_score == null ? "unranked" : report.priority_score) + '</strong></div></div><p class="reason">' + AppUtils.escape(report.priority_reason || "No priority reason provided") + '</p><div class="detail-actions">' + (report.status === "pending" ? '<button class="primary-btn" data-action="resolved">Confirm resolved</button><button class="danger-btn" data-action="rejected">Reject</button>' : '<span class="badge ' + report.status + '">Closed as ' + report.status + '</span>') + '</div></div>';
    detail.classList.add("open");
    detail.querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", () => act(report.id, button.dataset.action)));
  }
  async function act(id, status) { await API.updateStatus(id, status); const report = state.reports.find(item => item.id === id); if (report) report.status = status; detail.classList.remove("open"); renderList(); }
  document.querySelectorAll(".tab").forEach(tab => tab.addEventListener("click", () => { state.filter = tab.dataset.status; document.querySelectorAll(".tab").forEach(item => item.classList.toggle("active", item === tab)); renderList(); }));
  document.getElementById("modeSelect").addEventListener("change", async event => { state.mode = event.target.value; await API.updateSettings(state.mode); updateMode(); });
  function updateMode() { const el = document.getElementById("modeStatus"); el.textContent = state.mode === "model" ? "OFFLINE MODEL" : "CLOUD API"; el.className = "badge " + (state.mode === "model" ? "resolved" : "pending"); }
  document.getElementById("manualBtn").addEventListener("click", () => { state.manual = !state.manual; document.getElementById("manualBanner").classList.toggle("show", state.manual); });
  map.on("click", async event => { if (!state.manual) return; state.manual = false; document.getElementById("manualBanner").classList.remove("show"); const assetType = prompt("Asset type: road or building", "road"); if (!assetType || !["road", "building"].includes(assetType)) return; const stateValue = prompt(assetType === "road" ? "State: passable, impassable or unknown" : "State: damaged, not_damaged or unknown", "unknown"); if (!stateValue) return; const report = await API.createReport({ lat: event.latlng.lat, lon: event.latlng.lng, asset_type: assetType, state: stateValue, image_path: "mocks/images/report-6.svg" }); state.reports.unshift(report); refreshPins(); renderList(); select(report.id); });
  Promise.all([API.getReports(), API.getRoads(), API.getPockets(), API.getSettings()]).then(([reports, roads, pockets, settings]) => { state.reports = reports; state.mode = settings.detection_mode; document.getElementById("modeSelect").value = state.mode; updateMode(); renderMap(roads, pockets); refreshPins(); renderList(); });
})();
