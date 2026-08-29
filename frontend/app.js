(function () {
  const cfg = window.APP_CONFIG || { API_BASE_URL: "", USE_MOCKS: true };
  const mock = window.MOCK_DATA;
  const clone = value => JSON.parse(JSON.stringify(value));

  async function request(path, options) {
    if (!cfg.USE_MOCKS) {
      const response = await fetch((cfg.API_BASE_URL || "") + path, options);
      if (!response.ok) throw new Error("Request failed (" + response.status + ")");
      return response.status === 204 ? null : response.json();
    }
    await new Promise(resolve => setTimeout(resolve, 180));
    if (path === "/api/reports" && (!options || options.method === "GET")) return clone(mock.reports);
    if (path === "/api/roads") return clone(mock.roads);
    if (path === "/api/pockets") return clone(mock.pockets);
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
      const report = {
        id: nextId, image_path: payload.image_path || "mocks/images/report-1.svg",
        lat: Number(payload.lat), lon: Number(payload.lon), gps_accuracy_m: payload.gps_accuracy_m ? Number(payload.gps_accuracy_m) : null,
        asset_type: payload.asset_type || "road", state: payload.state || "unknown", confidence: 1,
        edge_id: null, n_reports: 1, status: "pending", detection_mode: "manual",
        priority_score: null, priority_reason: "Manually reported; awaiting network prioritisation", created_at: new Date().toISOString()
      };
      mock.reports.unshift(report);
      return clone(report);
    }
    const statusMatch = path.match(/^\/api\/reports\/(\d+)\/status$/);
    if (statusMatch && options && options.method === "POST") {
      const report = mock.reports.find(item => item.id === Number(statusMatch[1]));
      if (!report) throw new Error("Report not found");
      Object.assign(report, JSON.parse(options.body));
      return clone(report);
    }
    throw new Error("Mock endpoint not implemented: " + path);
  }

  window.API = {
    getReports: status => request("/api/reports" + (status ? "?status=" + status : "")),
    getReport: id => request("/api/reports/" + id),
    createReport: data => request("/api/reports", { method: "POST", body: data }),
    updateStatus: (id, status) => request("/api/reports/" + id + "/status", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }) }),
    getRoads: () => request("/api/roads"),
    getPockets: () => request("/api/pockets"),
    getSettings: () => request("/api/settings"),
    updateSettings: mode => request("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ detection_mode: mode }) })
  };

  window.AppUtils = {
    stateLabel: state => state.replace("_", " "),
    stateClass: state => ({ passable: "state-passable", impassable: "state-impassable", unknown: "state-unknown", damaged: "state-damaged", not_damaged: "state-passable" }[state] || "state-unknown"),
    markerColor: state => ({ passable: "#55706b", impassable: "#b83a3a", unknown: "#bf862c", damaged: "#b83a3a", not_damaged: "#55706b" }[state] || "#6a747a"),
    populationText: pocket => pocket.population_source === "missing" ? "unknown" : pocket.population_source === "raster" ? "est. " + pocket.population.toLocaleString() : pocket.population.toLocaleString(),
    sortReports: reports => reports.slice().sort((a, b) => (a.priority_score == null) - (b.priority_score == null) || (b.priority_score || 0) - (a.priority_score || 0)),
    escape: value => String(value).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[ch])),
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
    }
  };
})();
