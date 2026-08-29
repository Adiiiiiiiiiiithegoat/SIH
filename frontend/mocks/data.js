(function () {
  const bbox = { west: 74.78, south: 13.00, east: 74.86, north: 13.10 };
  const roadStates = ["passable", "impassable", "unknown"];
  const buildingStates = ["damaged", "not_damaged", "unknown"];
  const modes = ["api", "model", "manual"];
  const reports = [];
  for (let i = 1; i <= 30; i += 1) {
    const assetType = i % 3 === 0 ? "building" : "road";
    const state = assetType === "road" ? roadStates[i % 3] : buildingStates[i % 3];
    const status = i % 5 === 0 ? "resolved" : i % 7 === 0 ? "rejected" : "pending";
    const detectionMode = modes[i % 3];
    const priorityScore = i % 8 === 0 ? null : Number((9700 - i * 211.7).toFixed(1));
    reports.push({
      id: i,
      image_path: "mocks/images/report-" + ((i - 1) % 6 + 1) + ".svg",
      lat: Number((bbox.south + 0.006 + (i * 0.0027) % 0.088).toFixed(6)),
      lon: Number((bbox.west + 0.004 + (i * 0.0021) % 0.072).toFixed(6)),
      gps_accuracy_m: i % 4 === 0 ? null : Number((5 + (i % 9) * 1.5).toFixed(1)),
      asset_type: assetType,
      state: state,
      confidence: Number((0.62 + (i % 10) * 0.035).toFixed(2)),
      edge_id: assetType === "road" ? "edge-" + (9000 + i) : null,
      n_reports: i % 6 === 0 ? 4 : i % 4 === 0 ? 2 : 1,
      status: status,
      detection_mode: detectionMode,
      priority_score: priorityScore,
      priority_reason: priorityScore === null
        ? "Awaiting network context before priority can be ranked"
        : assetType === "road" && state === "impassable"
          ? "Severs access to Padmavathi Hospital for a nearby settlement"
          : assetType === "building" && state === "damaged"
            ? "Structural damage reported near the coastal evacuation route"
            : "Routine assessment; no immediate access loss detected",
      created_at: new Date(Date.UTC(2026, 7, 30, 8, (i * 3) % 60, i)).toISOString()
    });
  }
  const roads = Array.from({ length: 9 }, function (_, i) {
    const lon = 74.784 + i * 0.008;
    return {
      edge_id: "edge-" + (9001 + i),
      coordinates: [[13.004, lon], [13.046, lon + 0.003], [13.092, lon - 0.002]],
      state: roadStates[i % 3]
    };
  });
  const pockets = [
    {
      id: "pocket-a",
      polygon: [[13.024, 74.805], [13.041, 74.813], [13.039, 74.829], [13.018, 74.824]],
      population: 1240,
      population_source: "census",
      lost_facility: "Padmavathi Hospital",
      prior_access_km: 4.65
    },
    {
      id: "pocket-b",
      polygon: [[13.066, 74.826], [13.084, 74.836], [13.078, 74.852], [13.058, 74.845]],
      population: 860,
      population_source: "raster",
      lost_facility: "Surathkal Primary Health Centre",
      prior_access_km: 6.2
    }
  ];
  window.MOCK_DATA = { bbox, reports, roads, pockets, settings: { detection_mode: "model" } };
})();
