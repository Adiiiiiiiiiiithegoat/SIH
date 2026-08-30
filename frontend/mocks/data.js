(function () {
  const bbox = { west: 74.78, south: 13.00, east: 74.86, north: 13.10 };

  // ponytail: seeded LCG so the demo network is byte-identical on every reload.
  let seed = 20260830;
  const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  const jitter = amount => (rand() - 0.5) * amount;
  const r6 = value => Number(value.toFixed(6));

  // Metres per degree at ~13°N. Good enough for a display-only length.
  const M_LAT = 110600, M_LON = 108300;
  function metres(points) {
    let total = 0;
    for (let i = 1; i < points.length; i += 1) {
      total += Math.hypot((points[i][0] - points[i - 1][0]) * M_LAT, (points[i][1] - points[i - 1][1]) * M_LON);
    }
    return Number(total.toFixed(1));
  }

  // The Arabian Sea edge. Nothing is drawn west of it, so the coastline reads
  // as the absence of roads rather than as a drawn feature.
  const coast = lat => 74.7895 + 0.0055 * Math.sin((lat - 13) * 62) + 0.0035 * Math.sin((lat - 13) * 23 + 1.7);

  const roads = [];
  let seq = 9000;
  function add(highway, name, points) {
    roads.push({
      edge_id: "edge-" + (seq += 1),
      name: name,
      highway: highway,
      coordinates: points.map(point => [r6(point[0]), r6(point[1])]),
      state: "passable",
      // A span that is part bridge is the part that washes out, so the
      // dashboard marks it. Rare, as it is on the ground.
      bridge: rand() < 0.02,
      length_m: metres(points)
    });
  }
  function chop(highway, name, points) {
    for (let i = 1; i < points.length; i += 1) add(highway, name, [points[i - 1], points[i]]);
  }

  // NH 66 — the trunk spine running north to south just inland of the shore.
  const spine = [];
  for (let lat = 13.001; lat <= 13.0995; lat += 0.0025) spine.push([lat, 74.8125 + 0.0035 * Math.sin((lat - 13) * 40)]);
  chop("trunk", "NH 66", spine);

  // Inland arterial paralleling the Konkan railway.
  const inland = [];
  for (let lat = 13.003; lat <= 13.0985; lat += 0.0035) inland.push([lat, 74.8385 + 0.0028 * Math.sin((lat - 13) * 55 + 2)]);
  chop("primary", "Mangaluru–Udupi Road", inland);

  // Named east-west links running from the shore to the eastern edge.
  [[13.0085, "Kulai Cross Road"], [13.0235, "Hosabettu Road"], [13.0385, "Surathkal Beach Road"],
   [13.0535, "NITK Campus Road"], [13.0685, "Mukka Main Road"], [13.0855, "Sasihithlu Road"]
  ].forEach(function (link) {
    const points = [];
    for (let lon = coast(link[0]) + 0.0018; lon <= 74.8575; lon += 0.0062) points.push([link[0] + jitter(0.0007), lon]);
    chop("primary", link[1], points);
  });

  // Unnamed secondary grid.
  for (let lon = 74.8005; lon <= 74.855; lon += 0.0092) {
    const points = [];
    for (let lat = 13.0015; lat <= 13.0985; lat += 0.0058) points.push([lat, lon + jitter(0.0009)]);
    chop("secondary", null, points);
  }
  for (let lat = 13.0055; lat <= 13.098; lat += 0.0071) {
    const points = [];
    for (let lon = coast(lat) + 0.002; lon <= 74.857; lon += 0.0075) points.push([lat + jitter(0.0006), lon]);
    chop("secondary", null, points);
  }

  // Residential capillaries, denser near the trunk, absent over the sea.
  for (let lat = 13.0025; lat < 13.0985; lat += 0.0038) {
    for (let lon = 74.7975; lon < 74.858; lon += 0.0046) {
      if (lon < coast(lat) + 0.0012) continue;
      if (rand() > 0.95 - Math.min(0.55, Math.abs(lon - 74.8175) * 14)) continue;
      const count = 2 + Math.floor(rand() * 3);
      for (let k = 0; k < count; k += 1) {
        const y = lat + rand() * 0.0034, x = lon + rand() * 0.0042;
        if (x < coast(y)) continue;
        add("residential", null, rand() < 0.5
          ? [[y, x], [y, x + 0.0016 + rand() * 0.0022]]
          : [[y, x], [y + 0.0013 + rand() * 0.0018, x + jitter(0.0006)]]);
      }
    }
  }

  const trunk = roads.filter(road => road.highway === "trunk");
  const primary = roads.filter(road => road.highway === "primary");
  const secondary = roads.filter(road => road.highway === "secondary");
  const severedEdge = trunk[16];
  const severedEdgeB = primary[52];
  const severedEdgeC = secondary[74];
  // The demo needs one cut that is a bridge, so the marker is visible.
  severedEdge.bridge = true;
  severedEdgeB.bridge = false;
  severedEdgeC.bridge = false;

  const reports = [];
  const midpoint = edge => [
    (edge.coordinates[0][0] + edge.coordinates[1][0]) / 2,
    (edge.coordinates[0][1] + edge.coordinates[1][1]) / 2
  ];
  let nextId = 0;
  function report(edge, overrides) {
    const point = edge ? midpoint(edge) : [13.012 + rand() * 0.082, 74.802 + rand() * 0.05];
    nextId += 1;
    reports.push(Object.assign({
      id: nextId,
      image_path: "mocks/images/report-" + ((nextId - 1) % 6 + 1) + ".svg",
      lat: r6(point[0] + jitter(0.0005)),
      lon: r6(point[1] + jitter(0.0005)),
      gps_accuracy_m: null,
      asset_type: "road",
      state: "unknown",
      confidence: Number((0.62 + rand() * 0.36).toFixed(2)),
      edge_id: edge ? edge.edge_id : null,
      n_reports: 1,
      status: "pending",
      detection_mode: "model",
      priority_score: null,
      priority_reason: "Routine assessment; no access loss detected",
      created_at: new Date(Date.UTC(2026, 7, 30, 6 + (nextId % 9), (nextId * 7) % 60, nextId % 60)).toISOString()
    }, overrides || {}));
  }

  // The edges the system currently treats as cut.
  report(severedEdge, {
    state: "impassable", confidence: 0.94, n_reports: 4, gps_accuracy_m: 6.5,
    detection_mode: "api", priority_score: 9812.4,
    priority_reason: "Severs Hosabettu from Padmavathi Hospital; 8,420 residents lose their only route"
  });
  report(severedEdgeB, {
    state: "impassable", status: "resolved", confidence: 0.91, n_reports: 3, gps_accuracy_m: 4.2,
    detection_mode: "model", priority_score: 8104.0,
    priority_reason: "Cuts Mukka from the Surathkal Primary Health Centre approach"
  });
  // Second span cutting the same pocket as severedEdgeB — clearing either one
  // alone does not reconnect Mukka, which is what the detail panel has to say.
  report(severedEdgeC, {
    state: "impassable", confidence: 0.88, n_reports: 2, gps_accuracy_m: 9.0,
    detection_mode: "api", priority_score: 7960.5,
    priority_reason: "Second cut on the Mukka approach; the area stays severed while either span is blocked"
  });
  // Rejected impassable claim — must not count anywhere.
  report(trunk[31], {
    state: "impassable", status: "rejected", confidence: 0.55, detection_mode: "model",
    priority_score: 2140.6, priority_reason: "Photo shows standing water only; carriageway remained open"
  });

  const fillers = [
    { asset_type: "road", state: "passable", reason: "Debris cleared from the shoulder; carriageway open" },
    { asset_type: "road", state: "unknown", reason: "Image too dark to classify; awaiting a second report" },
    { asset_type: "building", state: "damaged", reason: "Structural damage beside the coastal evacuation route" },
    { asset_type: "road", state: "unknown", reason: "Water over the surface; depth not established" },
    { asset_type: "building", state: "not_damaged", reason: "Facade intact; no action required" },
    { asset_type: "road", state: "passable", reason: "Single lane usable past the fallen tree" },
    { asset_type: "building", state: "unknown", reason: "Frame obscured; classification withheld" }
  ];
  const modes = ["api", "model", "manual"];
  const pool = trunk.concat(primary, secondary);
  for (let i = 0; i < 27; i += 1) {
    const filler = fillers[i % fillers.length];
    const ranked = i % 7 !== 5;
    report(filler.asset_type === "road" ? pool[(i * 37) % pool.length] : null, {
      asset_type: filler.asset_type,
      state: filler.state,
      status: i % 6 === 4 ? "resolved" : i % 11 === 9 ? "rejected" : "pending",
      detection_mode: modes[i % 3],
      gps_accuracy_m: i % 4 === 3 ? null : Number((4 + (i % 9) * 1.6).toFixed(1)),
      n_reports: i % 6 === 0 ? 3 : i % 4 === 0 ? 2 : 1,
      edge_id: filler.asset_type === "road" ? pool[(i * 37) % pool.length].edge_id : null,
      priority_score: ranked ? Number((7420 - i * 233.4).toFixed(1)) : null,
      priority_reason: ranked ? filler.reason : "Awaiting network context before priority can be ranked"
    });
  }

  function hull(lat, lon, ry, rx) {
    const points = [];
    for (let angle = 0; angle < Math.PI * 2 - 0.01; angle += Math.PI / 5) {
      points.push([
        r6(lat + Math.sin(angle) * ry * (0.72 + rand() * 0.5)),
        r6(lon + Math.cos(angle) * rx * (0.72 + rand() * 0.5))
      ]);
    }
    return points;
  }

  // A severing edge carries the full span, per CONTRACT.md, so the map can
  // highlight it without a second lookup. report_ids are stitched on by the
  // mock's recompute(), the way pipeline.recompute() does it.
  function severingEdge(road) {
    return {
      edge_id: road.edge_id,
      name: road.name || "<unnamed>",
      length_m: road.length_m,
      highway: road.highway,
      bridge: !!road.bridge,
      coordinates: road.coordinates,
      // The live reports asserting this span is impassable, ascending — what
      // pipeline.recompute() stitches on. recompute() in app.js keeps these in
      // sync as the operator works; this is the state on first load.
      report_ids: reports
        .filter(item => item.asset_type === "road" && item.edge_id === road.edge_id &&
          item.state === "impassable" && item.status !== "rejected")
        .map(item => item.id).sort((a, b) => a - b)
    };
  }

  const pockets = [
    {
      id: "pocket-a", polygon: hull(13.0245, 74.8255, 0.0105, 0.0092),
      centroid: [13.0245, 74.8255], n_nodes: 214, hull_area_km2: 1.842, hull_widened: false,
      population: 8420, population_source: "census", population_method: "census-2011",
      population_coverage: "full", population_settlements: ["Hosabettu", "Kana"],
      lost_facility: "Padmavathi Hospital, Dakshina Kannada", prior_access_km: 4.65,
      nearest_place: "Hosabettu", nearest_place_km: 0.42,
      severing_edges: [severingEdge(severedEdge)]
    },
    {
      id: "pocket-b", polygon: hull(13.0705, 74.8425, 0.0092, 0.0078),
      centroid: [13.0705, 74.8425], n_nodes: 131, hull_area_km2: 1.114, hull_widened: false,
      population: 3653, population_source: "raster", population_method: "cells",
      population_coverage: "partial", population_settlements: [],
      lost_facility: "Surathkal Primary Health Centre", prior_access_km: 6.2,
      nearest_place: "Mukka", nearest_place_km: 1.07,
      // Two spans cut this one together — exercises the multi-edge copy.
      severing_edges: [severingEdge(severedEdgeB), severingEdge(severedEdgeC)]
    }
  ];

  // Pockets the backend would compute once a new cut is reported. The mock
  // reveals the nearest one so the live demo has something to show.
  const latentPockets = [
    {
      id: "pocket-c", polygon: hull(13.0555, 74.8125, 0.0088, 0.0075),
      centroid: [13.0555, 74.8125], n_nodes: 96, hull_area_km2: 0.735, hull_widened: false,
      population: 4180, population_source: "raster", population_method: "cells",
      population_coverage: "full", population_settlements: [],
      lost_facility: "Mukka Health Sub-centre", prior_access_km: 3.4,
      nearest_place: "Mukka", nearest_place_km: 0.63, severing_edges: []
    },
    {
      id: "pocket-d", polygon: hull(13.0885, 74.8345, 0.0075, 0.0068),
      centroid: [13.0885, 74.8345], n_nodes: 74, hull_area_km2: 0.482, hull_widened: false,
      population: 2260, population_source: "census", population_method: "census-2011",
      population_coverage: "full", population_settlements: ["Sasihithlu"],
      lost_facility: "Sasihithlu Primary Health Centre", prior_access_km: 5.1,
      nearest_place: "Sasihithlu", nearest_place_km: 0.28, severing_edges: []
    },
    {
      id: "pocket-e", polygon: hull(13.0105, 74.8205, 0.0072, 0.0065),
      centroid: [13.0105, 74.8205], n_nodes: 58, hull_area_km2: 0.061, hull_widened: true,
      population: null, population_source: "missing", population_method: "nodata-only",
      population_coverage: "outside", population_settlements: [],
      lost_facility: "Kulai Dispensary", prior_access_km: 2.8,
      nearest_place: "Kulai", nearest_place_km: 0.91, severing_edges: []
    }
  ];

  window.MOCK_DATA = {
    bbox, reports, roads, pockets, latentPockets, severingEdge,
    settings: { detection_mode: "model" }
  };
})();
