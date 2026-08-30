(function () {
  const cfg = window.APP_CONFIG || { API_BASE_URL: "", USE_MOCKS: true };
  const clone = value => JSON.parse(JSON.stringify(value));

  // mocks/data.js loads after this file, so reach for it lazily.
  const data = () => window.MOCK_DATA;

  const midpoint = edge => [
    (edge.coordinates[0][0] + edge.coordinates[edge.coordinates.length - 1][0]) / 2,
    (edge.coordinates[0][1] + edge.coordinates[edge.coordinates.length - 1][1]) / 2
  ];
  const near = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

  function nearestEdge(lat, lon) {
    let best = null, bestDistance = Infinity;
    data().roads.forEach(function (road) {
      const distance = near([lat, lon], midpoint(road));
      if (distance < bestDistance) { bestDistance = distance; best = road; }
    });
    return bestDistance < 0.004 ? best : null;
  }

  // Only an OPEN case holds a road closed -- exactly app/pipeline.LIVE. A
  // resolved case means crews cleared the road, a dismissed one means it was
  // never blocked; neither may keep a span cut or a pocket severed. Every
  // "is this road still blocked?" question in the frontend goes through here,
  // so the headline figures cannot drift from the backend's severance picture.
  const LIVE_STATUS = ["pending", "in_progress"];
  const isLive = report => LIVE_STATUS.indexOf(report && report.status) !== -1;

  // Stands in for the backend recomputing road state and severance after a
  // write. Road state mirrors app/pipeline.roads(): impassable beats unknown
  // beats passable, and closed reports are ignored.
  function recompute() {
    const mock = data();
    const rank = { impassable: 3, unknown: 2, passable: 1 };
    const reported = {};
    mock.reports.forEach(function (report) {
      if (report.asset_type !== "road" || !report.edge_id || !isLive(report)) return;
      if ((rank[report.state] || 0) > (rank[reported[report.edge_id]] || 0)) reported[report.edge_id] = report.state;
    });
    mock.roads.forEach(road => { road.state = reported[road.edge_id] || "passable"; });

    const cut = mock.roads.filter(road => road.state === "impassable");
    const cutIds = cut.map(road => road.edge_id);
    mock.pockets.slice().forEach(function (pocket) {
      if (pocket.severing_edges.every(edge => cutIds.indexOf(edge.edge_id) !== -1)) return;
      mock.pockets.splice(mock.pockets.indexOf(pocket), 1);
      if (pocket.latent) mock.latentPockets.push(pocket);
    });
    cut.forEach(function (road) {
      if (mock.pockets.some(pocket => pocket.severing_edges.some(edge => edge.edge_id === road.edge_id))) return;
      if (!mock.latentPockets.length) return;
      const point = midpoint(road);
      mock.latentPockets.sort((a, b) => near(point, a.centroid) - near(point, b.centroid));
      const pocket = mock.latentPockets.shift();
      pocket.severing_edges = [mock.severingEdge(road)];
      pocket.latent = true;
      mock.pockets.push(pocket);
    });

    // The severance engine knows nothing about reports; pipeline.recompute()
    // stitches the link on afterwards, so the mock does too.
    const blocking = {};
    mock.reports.forEach(function (report) {
      if (report.asset_type !== "road" || !report.edge_id) return;
      if (report.state !== "impassable" || !isLive(report)) return;
      (blocking[report.edge_id] = blocking[report.edge_id] || []).push(report.id);
    });
    mock.pockets.forEach(pocket => pocket.severing_edges.forEach(function (edge) {
      edge.report_ids = (blocking[edge.edge_id] || []).slice().sort((a, b) => a - b);
    }));
  }

  let primed = false;

  async function request(path, options) {
    if (!cfg.USE_MOCKS) {
      const response = await fetch((cfg.API_BASE_URL || "") + path, options);
      if (!response.ok) throw new Error("Request failed (" + response.status + ")");
      return response.status === 204 ? null : response.json();
    }
    const mock = data();
    if (!primed) { primed = true; recompute(); }
    await new Promise(resolve => setTimeout(resolve, 180));
    if (path === "/api/reports" && (!options || options.method === "GET")) return clone(mock.reports);
    if (path === "/api/roads") return clone(mock.roads);
    if (path === "/api/pockets") return clone(mock.pockets);
    if (path === "/api/detours") return clone(mock.detours || []);
    if (path === "/api/settings" && (!options || options.method === "GET")) return clone(mock.settings);
    if (path === "/api/settings" && options && options.method === "POST") {
      mock.settings = JSON.parse(options.body);
      return clone(mock.settings);
    }
    if (path === "/api/reports" && options && options.method === "POST") {
      const nextId = Math.max.apply(null, mock.reports.map(report => report.id)) + 1;
      const payload = typeof FormData !== "undefined" && options.body instanceof FormData
        ? Object.fromEntries(options.body.entries())
        : (typeof options.body === "string" ? JSON.parse(options.body) : options.body);
      const lat = Number(payload.lat), lon = Number(payload.lon);
      const edge = payload.asset_type === "building" ? null : nearestEdge(lat, lon);
      const report = {
        id: nextId, image_path: payload.image_path || "mocks/images/report-1.svg",
        lat: lat, lon: lon, gps_accuracy_m: payload.gps_accuracy_m ? Number(payload.gps_accuracy_m) : null,
        asset_type: payload.asset_type || "road", state: payload.state || "unknown", confidence: 1,
        edge_id: edge ? edge.edge_id : null, n_reports: 1, status: "pending", detection_mode: "manual",
        priority_score: null, priority_reason: "Manually reported; awaiting network prioritisation",
        created_at: new Date().toISOString()
      };
      mock.reports.unshift(report);
      recompute();
      return clone(report);
    }
    const single = path.match(/^\/api\/reports\/(\d+)$/);
    if (single && (!options || options.method === "GET")) {
      const report = mock.reports.find(item => item.id === Number(single[1]));
      if (!report) throw new Error("Report not found");
      // Synthesise a candidate shortlist the way binding.candidates() would:
      // the bound edge first, then the next-nearest spans by midpoint.
      const here = [report.lat, report.lon];
      const ranked = mock.roads
        .map(road => ({ road: road, d: near(here, midpoint(road)) }))
        .sort((a, b) => a.d - b.d).slice(0, 5);
      const cands = ranked.map(x => ({
        edge_id: x.road.edge_id,
        distance_m: Math.round(x.d * 111000),
        score: Number(Math.max(0, 1 - x.d / 0.01).toFixed(4))
      }));
      if (report.edge_id && !cands.some(c => c.edge_id === report.edge_id)) {
        cands.unshift({ edge_id: report.edge_id, distance_m: 0, score: 1 });
      }
      return clone(Object.assign({}, report, { bind_candidates: cands, bearing: null }));
    }
    const write = path.match(/^\/api\/reports\/(\d+)\/(status|state|edge)$/);
    if (write && options && options.method === "POST") {
      const report = mock.reports.find(item => item.id === Number(write[1]));
      if (!report) throw new Error("Report not found");
      const body = JSON.parse(options.body);
      if (write[2] === "state") {
        // An override is a human assertion, exactly as the backend records it.
        Object.assign(report, { state: body.state, confidence: 1, detection_mode: "manual" });
      } else {
        Object.assign(report, body);
      }
      recompute();
      return clone(report);
    }
    throw new Error("Mock endpoint not implemented: " + path);
  }

  window.API = {
    getReports: status => request("/api/reports" + (status ? "?status=" + status : "")),
    getReport: id => request("/api/reports/" + id),
    createReport: data => request("/api/reports", { method: "POST", body: data }),
    updateStatus: (id, status) => request("/api/reports/" + id + "/status", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }) }),
    updateState: (id, state) => request("/api/reports/" + id + "/state", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state }) }),
    updateEdge: (id, edge_id) => request("/api/reports/" + id + "/edge", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ edge_id }) }),
    getRoads: () => request("/api/roads"),
    getPockets: () => request("/api/pockets"),
    getDetours: () => request("/api/detours"),
    getSettings: () => request("/api/settings"),
    setSettings: body => request("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
  };

  // Every enum in CONTRACT.md gets its display text here and nowhere else.
  const LABELS = {
    status: {
      pending: "Pending review", in_progress: "Work in progress",
      resolved: "Resolved", rejected: "Dismissed"
    },
    status_short: {
      pending: "Pending", in_progress: "In progress",
      resolved: "Resolved", rejected: "Dismissed"
    },
    detection_mode: { api: "Cloud API", manual: "Manual entry" },
    state: {
      passable: "Passable", impassable: "Impassable", unknown: "Unknown",
      damaged: "Damaged", not_damaged: "Not damaged"
    },
    asset_type: { road: "Road", building: "Building" },
    population_source: { census: "Census 2011", raster: "Raster estimate", missing: "No population data" },
    population_coverage: {
      full: "Full coverage", partial: "Partial coverage — undercount",
      outside: "Outside the raster", "no-raster": "No raster available"
    },
    // Raw OSM classes. "unclassified" is a real class meaning a minor public
    // road, not an unknown one — labelling it "Unknown" would be a lie.
    highway: {
      motorway: "Motorway", trunk: "Trunk road", primary: "Primary road",
      secondary: "Secondary road", tertiary: "Tertiary road",
      residential: "Residential street", unclassified: "Minor road",
      living_street: "Living street", service: "Service road", track: "Track",
      motorway_link: "Motorway ramp", trunk_link: "Trunk ramp",
      primary_link: "Primary ramp", secondary_link: "Secondary ramp",
      tertiary_link: "Tertiary ramp"
    }
  };

  // Free, keyless raster tile sources, shared by both maps so they cannot
  // silently drift to different looks. Both come from Esri (one attribution
  // story, one ToS): World Light Gray Canvas is the pale base with data drawn
  // on top of it, which is exactly what this app does; World Imagery is the
  // satellite alternative a viewer can switch to, same as Google Maps' own
  // toggle. CARTO's basemaps.cartocdn.com was tried first and dropped -- it
  // now serves an "API KEY REQUIRED" watermark baked into the tile image
  // itself, which still returns HTTP 200 and so passes a naive "did the
  // request succeed" check; only actually opening a returned tile caught it.
  // The gray canvas ships with no labels by design -- `labelsUrl` is Esri's
  // companion transparent overlay (place names, boundaries) stacked on top.
  // Plain config here, not `L.tileLayer(...)` calls -- this file loads in a
  // Node test sandbox with no Leaflet, so it must stay Leaflet-free.
  const BASEMAPS = {
    map: {
      url: "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
      labelsUrl: "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
      attribution: "Esri, HERE, Garmin, &copy; OpenStreetMap contributors",
      options: { maxZoom: 16 }
    },
    satellite: {
      url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      attribution: "Imagery &copy; Esri",
      options: { maxZoom: 19 }
    }
  };

  // One source of colour for anything drawn on the map. Mirrors the semantic
  // tokens in styles.css; nothing else picks its own hex.
  const COLORS = {
    bad: "#c02418",
    good: "#1f7a4d",
    unknown: "#c2610a",
    neutral: "#b9c0c5",   // the road network, recessive on a light ground
    affected: "#7d878d"
  };

  const WEIGHTS = { motorway: 2.6, trunk: 2.4, primary: 2, secondary: 1.5, tertiary: 1.2, residential: 1 };

  // Shadows the deprecated global window.escape, deliberately — every string
  // interpolated into panel HTML goes through this one.
  const escape = value => String(value).replace(/[&<>"']/g,
    ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[ch]));


  window.AppUtils = {
    LABELS: LABELS,
    COLORS: COLORS,
    BASEMAPS: BASEMAPS,
    label: (kind, value) => (LABELS[kind] && LABELS[kind][value]) || (value == null ? "Unknown" : String(value)),
    stateLabel: state => (LABELS.state[state] || "Unknown"),
    stateClass: state => ({ passable: "good", impassable: "bad", unknown: "unknown", damaged: "bad", not_damaged: "good" }[state] || "neutral"),
    statusClass: status => ({ pending: "warn", in_progress: "ok", resolved: "neutral", rejected: "neutral" }[status] || "neutral"),

    // A report is only holding a road closed while it is still open work.
    // Mirrors app/pipeline.LIVE -- the one rule the map, the pins and the
    // headline figures all read, so none of them can claim a road is cut
    // after the backend has reopened it.
    isLive: isLive,

    // Where a report sits in the workflow. Any status can reach any other --
    // the operator decides -- but these are the moves worth a button.
    nextStatuses: status => ({
      pending: ["in_progress", "rejected"],
      in_progress: ["resolved", "pending", "rejected"],
      resolved: ["in_progress", "pending"],
      rejected: ["pending", "in_progress"]
    }[status] || []),

    // Button text for a workflow move. The same destination means different
    // things from different lanes: moving a CLOSED case to in_progress is
    // reopening a case someone already finished, not accepting a new one, and
    // labelling it "Accept -- start work" invites an operator to reopen a
    // cleared road by reflex. So the label is keyed by both ends of the move.
    ACTIONS: {
      pending: { in_progress: "Accept — start work", resolved: "Mark resolved", rejected: "Dismiss" },
      in_progress: { resolved: "Mark resolved", pending: "Send back to pending", rejected: "Dismiss" },
      resolved: { in_progress: "Reopen — road still blocked", pending: "Reopen as pending", rejected: "Dismiss" },
      rejected: { pending: "Reinstate as pending", in_progress: "Reinstate — start work", resolved: "Mark resolved" }
    },
    actionLabel: function (from, to) {
      return (this.ACTIONS[from] || {})[to] || "Move to " + this.label("status", to).toLowerCase();
    },

    // A queue of raw scores spanning 0 to 4,000 is unreadable, and a column of
    // fifteen zeroes reads as broken rather than as "no access impact". The
    // band is what an operator triages on; the number stays for ordering.
    priorityBand: function (score) {
      if (score == null) return { key: "unranked", label: "Not ranked" };
      if (score >= 2000) return { key: "critical", label: "Critical" };
      if (score >= 500) return { key: "high", label: "High" };
      if (score >= 50) return { key: "moderate", label: "Moderate" };
      if (score > 0) return { key: "low", label: "Low" };
      return { key: "none", label: "No impact" };
    },

    timeAgo: function (iso) {
      const t = Date.parse(iso);
      if (!t) return "Unknown";
      const mins = Math.floor((Date.now() - t) / 60000);
      if (mins < 1) return "just now";
      if (mins < 60) return mins + " min ago";
      const hours = Math.floor(mins / 60);
      if (hours < 24) return hours + (hours === 1 ? " hour ago" : " hours ago");
      const days = Math.floor(hours / 24);
      return days + (days === 1 ? " day ago" : " days ago");
    },
    markerColor: state => ({ passable: COLORS.good, impassable: COLORS.bad, unknown: COLORS.unknown, damaged: COLORS.bad, not_damaged: COLORS.good }[state] || COLORS.neutral),

    // Highway class drives line weight; fall back to span length when the
    // payload carries no class, so long spans still read as through-routes.
    roadWeight: road => WEIGHTS[road && road.highway] || ((road && road.length_m > 400) ? 1.3 : 1),

    // A raster estimate is never shown as a plain count, and a pocket with no
    // population data is never shown as zero.
    // The backend keeps a raster estimate to one decimal, deliberately. People
    // are whole, so the rounding happens here, at the display boundary — the
    // uncertainty is carried by "est." and population_source, not by "681.6".
    populationText: pocket => (pocket.population == null || pocket.population_source === "missing")
      ? "unknown"
      : (pocket.population_source === "raster" ? "est. " : "") +
        Math.round(pocket.population).toLocaleString(),

    // Headline system state, derived from whatever reports and pockets are
    // loaded. Road state mirrors the backend exactly: a span counts as cut
    // only while a LIVE report says so. Counting a resolved case here is what
    // produced "2 roads impassable" beside a single cut-off area.
    //
    // `roads` is optional and only used to tell which impassable edges are
    // bridges -- omit it and bridgesImpassable is just 0, so old callers (and
    // tests) that only pass reports/pockets keep working.
    impactSummary: function (reports, pockets, roads) {
      const bridgeEdges = new Set((roads || []).filter(r => r.bridge).map(r => r.edge_id));
      const edges = new Set(), bridges = new Set();
      (reports || []).forEach(function (report) {
        if (report.asset_type !== "road" || report.state !== "impassable" || !isLive(report)) return;
        const eid = report.edge_id || "report-" + report.id;
        edges.add(eid);
        if (bridgeEdges.has(eid)) bridges.add(eid);
      });
      const buildingsDamaged = (reports || []).filter(report =>
        report.asset_type === "building" && report.state === "damaged" && isLive(report)).length;
      const list = pockets || [];
      let people = 0, counted = 0, estimated = false, incomplete = false;
      list.forEach(function (pocket) {
        if (pocket.population == null || pocket.population_source === "missing") { incomplete = true; return; }
        if (pocket.population_source === "raster") estimated = true;
        people += pocket.population;
        counted += 1;
      });
      return {
        roadsImpassable: edges.size,
        bridgesImpassable: bridges.size,
        buildingsDamaged: buildingsDamaged,
        areasSevered: list.length,
        peopleAffected: list.length === 0 ? 0 : (counted ? Math.round(people) : null),
        estimated: estimated,
        incomplete: incomplete
      };
    },

    // "8,420 people" vs "≈4,180 people" vs "Unknown" — never a bare zero for
    // a figure we could not measure.
    peopleText: function (summary) {
      if (summary.peopleAffected == null) return "unknown";
      return (summary.estimated ? "≈" : "") + summary.peopleAffected.toLocaleString();
    },
    populationCaveat: function (summary) {
      if (summary.peopleAffected == null) return "No population data for any severed area";
      if (summary.estimated && summary.incomplete) return "Partly raster-estimated; one area has no population data";
      if (summary.incomplete) return "Excludes one area with no population data";
      if (summary.estimated) return "Includes raster estimates, not census counts";
      return "";
    },

    sortReports: reports => reports.slice().sort((a, b) => (a.priority_score == null) - (b.priority_score == null) || (b.priority_score || 0) - (a.priority_score || 0)),
    escape: escape,

    // One option in the "re-bind this report" picker. `road` is the matching
    // record from GET /api/roads, or undefined for an edge not in the layer.
    bindOptionLabel: function (cand, road) {
      const name = road && road.name && road.name !== "<unnamed>" ? road.name : "Unnamed span";
      const parts = [name];
      if (cand && cand.distance_m != null) parts.push(Math.round(cand.distance_m) + " m away");
      return parts.join(" · ");
    },
    // The candidate shortlist, with the currently-bound edge guaranteed present
    // and marked -- an operator may have already overridden to an off-list edge.
    bindOptions: function (report, roadsById) {
      const cands = (report && report.bind_candidates) || [];
      const out = cands.map(c => ({
        edge_id: c.edge_id,
        label: this.bindOptionLabel(c, roadsById && roadsById[c.edge_id]),
        current: c.edge_id === report.edge_id
      }));
      if (report && report.edge_id && !out.some(o => o.current)) {
        out.unshift({
          edge_id: report.edge_id,
          label: this.bindOptionLabel({ edge_id: report.edge_id }, roadsById && roadsById[report.edge_id]) + " · current",
          current: true
        });
      }
      return out;
    },

    // A button whose click fires a slow request: disable it and flip
    // [aria-busy] (CSS swaps in a spinner) until the promise settles, so a
    // pending action doesn't read as a frozen page. Safe if the button is
    // torn out of the DOM mid-flight -- the restore just no-ops.
    busy: async function (btn, fn) {
      if (btn) { btn.setAttribute("aria-busy", "true"); btn.disabled = true; }
      try { return await fn(); }
      finally { if (btn) { btn.removeAttribute("aria-busy"); btn.disabled = false; } }
    },

    // --- severed pockets ---------------------------------------------------

    // The contract carries severing_edges as full span records. Tolerate a
    // bare edge_id too, so a stale payload degrades to "highlight the line"
    // rather than throwing.
    severingEdges: pocket => ((pocket && pocket.severing_edges) || [])
      .map(edge => (typeof edge === "string" ? { edge_id: edge } : edge))
      .filter(edge => edge && edge.edge_id),
    severingEdgeIds: function (pocket) {
      return this.severingEdges(pocket).map(edge => edge.edge_id);
    },
    // Reports asserting those spans are impassable — the photos behind the cut.
    pocketReportIds: function (pocket) {
      const seen = [];
      this.severingEdges(pocket).forEach(edge => (edge.report_ids || []).forEach(function (id) {
        if (seen.indexOf(id) === -1) seen.push(id);
      }));
      return seen.sort((a, b) => a - b);
    },

    kmText: km => (km == null ? "Not recorded" : km.toFixed(2) + " km"),
    metresText: m => (m == null ? "Not recorded" : Math.round(m).toLocaleString() + " m"),
    // Pockets run from ~0.01 km² up; a fixed 2dp would print "0.01" for most.
    areaText: km2 => (km2 == null ? "Not recorded"
      : (km2 < 0.1 ? km2.toFixed(3) : km2.toFixed(2)) + " km²"),
    highwayLabel: function (value) {
      if (LABELS.highway[value]) return LABELS.highway[value];
      if (!value) return "Unclassified";
      const words = String(value).replace(/_/g, " ");
      return words.charAt(0).toUpperCase() + words.slice(1);
    },

    RetryQueue: class {
      constructor(worker) { this.worker = worker; this.items = []; this.running = false; }
      enqueue(item) { this.items.push(item); return this.flush(); }
      async flush() {
        if (this.running) return;
        this.running = true;
        while (this.items.length) {
          try { await this.worker(this.items[0]); this.items.shift(); }
          catch (error) { break; }
        }
        this.running = false;
      }
      get size() { return this.items.length; }
    }
  };
  window.UploadUtils = {
    exifCoord: (ref, values) => {
      if (!values || values.length < 3) return null;
      const decimal = values[0] + values[1] / 60 + values[2] / 3600;
      return ref === "S" || ref === "W" ? -decimal : decimal;
    },

    // Nominatim (OpenStreetMap's own geocoder) is free, needs no key, and
    // shares the OSM lineage the rest of this project is built on -- a
    // citizen typing "Surathkal Beach Road" gets the same road the graph
    // knows about, not a mismatched third-party address index.
    // `bounded=1` makes the viewbox a hard filter, not a ranking hint: a
    // result outside the coverage area would not bind to any edge anyway, so
    // it should not show up as a plausible choice. A small pad softens the
    // edge of the report bbox without abandoning the filter altogether.
    geocodeUrl: function (bbox, query, limit) {
      const pad = 0.01; // ~1 km slack in this latitude band
      const viewbox = [bbox.west - pad, bbox.north + pad, bbox.east + pad, bbox.south - pad].join(",");
      const params = new URLSearchParams({
        format: "json", q: query, viewbox: viewbox, bounded: "1",
        countrycodes: "in", limit: String(limit || 6)
      });
      return "https://nominatim.openstreetmap.org/search?" + params.toString();
    },

    // Nominatim's display_name is one long comma string, most-specific
    // first ("Surathkal Beach Road, Surathkal, Mangaluru taluk, Dakshina
    // Kannada, Karnataka, 575014, India"). The result list shows the match
    // as a name, with a short trail of context underneath -- not the whole
    // string, which wraps into unreadable clutter at this panel's width.
    placeLabel: function (result) {
      const parts = String((result && result.display_name) || "")
        .split(",").map(s => s.trim()).filter(Boolean);
      return {
        name: parts[0] || "Unnamed location",
        detail: parts.slice(1, 4).join(", ")
      };
    },

    // Keystroke-per-request would hammer Nominatim's shared public instance
    // (its usage policy caps light use at ~1 request/second) and race
    // itself: a slow response to an early keystroke could land after a fast
    // response to a later one and show stale results. Debouncing the call
    // is necessary; cancelling the in-flight request on the next keystroke
    // (via AbortController, at the call site) is what actually prevents the
    // race -- this only throttles how often a call is attempted.
    debounce: function (fn, wait) {
      let timer = null;
      return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), wait);
      };
    }
  };
})();
