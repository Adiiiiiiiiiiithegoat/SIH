const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const context = { window: {}, console, setTimeout, clearTimeout };
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8"), context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "mocks", "data.js"), "utf8"), context);
const { AppUtils } = context.window;
assert.equal(context.window.UploadUtils.exifCoord("N", [13, 3, 5.4]).toFixed(6), "13.051500");
assert.equal(context.window.UploadUtils.exifCoord("W", [74, 46, 55.56]).toFixed(6), "-74.782100");
assert.equal(context.window.UploadUtils.exifCoord("N", null), null);
assert.equal(AppUtils.markerColor("impassable"), "#b83a3a");
assert.equal(AppUtils.markerColor("unknown"), "#bf862c");
assert.deepEqual(AppUtils.sortReports([{ priority_score: null }, { priority_score: 10 }, { priority_score: 20 }]).map(r => r.priority_score), [20, 10, null]);
assert.equal(AppUtils.populationText({ population: 100, population_source: "census" }), "100");
assert.equal(AppUtils.populationText({ population: 100, population_source: "raster" }), "est. 100");
assert.equal(AppUtils.populationText({ population: 0, population_source: "missing" }), "unknown");
context.window.API = {
  createReport: async data => {
    const id = context.window.MOCK_DATA.reports.length + 1;
    const report = { id, lat: data.lat, lon: data.lon, asset_type: data.asset_type, state: data.state, status: "pending" };
    context.window.MOCK_DATA.reports.push(report);
    return report;
  }
};
context.window.API.createReport({ lat: 13.05, lon: 74.81, asset_type: "road", state: "unknown" }).then(report => {
  assert.equal(report.status, "pending");
  assert.equal(context.window.MOCK_DATA.reports.at(-1).id, report.id);
});
const queue = new AppUtils.RetryQueue(async item => { if (item === "retry" && !queue.didRetry) { queue.didRetry = true; throw new Error("offline"); } });
queue.enqueue("retry");
setTimeout(() => {
  assert.equal(queue.size, 1);
  queue.flush().then(() => { assert.equal(queue.size, 0); console.log("PASS contract helpers, sorting, population rules, retry queue"); });
}, 0);
