const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const context = { window: {}, console, setTimeout, clearTimeout, URLSearchParams };
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8"), context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "mocks", "data.js"), "utf8"), context);
const { AppUtils } = context.window;

/* --- EXIF coordinates --- */
assert.equal(context.window.UploadUtils.exifCoord("N", [13, 3, 5.4]).toFixed(6), "13.051500");
assert.equal(context.window.UploadUtils.exifCoord("W", [74, 46, 55.56]).toFixed(6), "-74.782100");
assert.equal(context.window.UploadUtils.exifCoord("N", null), null);

/* --- geocode search (Nominatim) --- */
const BBOX = { west: 74.78, south: 13.00, east: 74.86, north: 13.10 };
const { geocodeUrl, placeLabel, debounce } = context.window.UploadUtils;

const url = new URL(geocodeUrl(BBOX, "Surathkal Beach Road", 6));
assert.equal(url.hostname, "nominatim.openstreetmap.org");
assert.equal(url.searchParams.get("q"), "Surathkal Beach Road");
assert.equal(url.searchParams.get("bounded"), "1", "outside-coverage results must be filtered, not just ranked lower");
assert.equal(url.searchParams.get("limit"), "6");
// viewbox is left,top,right,bottom (west,north,east,south) with a small pad.
const vb = url.searchParams.get("viewbox").split(",").map(Number);
assert.ok(vb[0] < BBOX.west && vb[2] > BBOX.east, "padded west/east straddle the report bbox");
assert.ok(vb[1] > BBOX.north && vb[3] < BBOX.south, "padded north/south straddle the report bbox");
// A default limit is used when none is given, not "limit=undefined".
assert.equal(new URL(geocodeUrl(BBOX, "x")).searchParams.get("limit"), "6");
// A query with spaces and symbols must survive URL encoding round-trip.
assert.equal(new URL(geocodeUrl(BBOX, "NH 66 & Mukka")).searchParams.get("q"), "NH 66 & Mukka");

const label = placeLabel({ display_name: "Surathkal Beach Road, Surathkal, Mangaluru taluk, Dakshina Kannada, Karnataka, 575014, India" });
assert.equal(label.name, "Surathkal Beach Road");
assert.equal(label.detail, "Surathkal, Mangaluru taluk, Dakshina Kannada");
// Missing/empty display_name never renders as blank or "undefined".
assert.equal(placeLabel({}).name, "Unnamed location");
assert.equal(placeLabel({ display_name: "" }).name, "Unnamed location");
assert.equal(placeLabel({ display_name: "Just One Thing" }).name, "Just One Thing");
assert.equal(placeLabel({ display_name: "Just One Thing" }).detail, "");

/* --- debounce: coalesces bursts into one trailing call --- */
(function () {
  let calls = 0;
  const bounced = debounce(() => { calls += 1; }, 20);
  bounced(); bounced(); bounced();
  assert.equal(calls, 0, "must not fire before the wait elapses");
  setTimeout(() => {
    assert.equal(calls, 1, "three rapid calls collapse into exactly one");
  }, 40);
})();

/* --- basemap config: both pages must share one look, no Leaflet needed to check it --- */
["map", "satellite"].forEach(function (key) {
  const b = AppUtils.BASEMAPS[key];
  assert.ok(b && b.url && b.attribution, key + " basemap has a url and attribution");
  ["{z}", "{x}", "{y}"].forEach(ph => assert.ok(b.url.includes(ph), key + " url has a " + ph + " placeholder"));
});
assert.notEqual(AppUtils.BASEMAPS.map.url, AppUtils.BASEMAPS.satellite.url);

/* --- map colours come from the semantic token set, not per-component hexes --- */
assert.equal(AppUtils.markerColor("impassable"), AppUtils.COLORS.bad);
assert.equal(AppUtils.markerColor("damaged"), AppUtils.COLORS.bad);
assert.equal(AppUtils.markerColor("unknown"), AppUtils.COLORS.unknown);
assert.equal(AppUtils.markerColor("not_damaged"), AppUtils.COLORS.good);
assert.equal(AppUtils.markerColor("nonsense"), AppUtils.COLORS.neutral);

/* --- highway class drives line weight, length is the fallback --- */
assert.ok(AppUtils.roadWeight({ highway: "trunk" }) > AppUtils.roadWeight({ highway: "residential" }));
assert.ok(AppUtils.roadWeight({ highway: "primary" }) > AppUtils.roadWeight({ highway: "secondary" }));
assert.equal(AppUtils.roadWeight({ length_m: 900 }), 1.3);
assert.equal(AppUtils.roadWeight({ length_m: 90 }), 1);

/* --- copy mapping: no enum value reaches the UI --- */
assert.equal(AppUtils.label("status", "pending"), "Pending review");
assert.equal(AppUtils.label("status", "in_progress"), "Work in progress");
assert.equal(AppUtils.label("status", "resolved"), "Resolved");
assert.equal(AppUtils.label("status", "rejected"), "Dismissed");
assert.ok(!AppUtils.label("status", "in_progress").includes("_"));
assert.ok(!AppUtils.label("status_short", "in_progress").includes("_"));
assert.equal(AppUtils.label("detection_mode", "api"), "Cloud API");
assert.equal(AppUtils.label("detection_mode", "model"), "Offline model");
assert.equal(AppUtils.label("detection_mode", "manual"), "Manual entry");
assert.equal(AppUtils.label("asset_type", "building"), "Building");
assert.equal(AppUtils.stateLabel("not_damaged"), "Not damaged");
assert.equal(AppUtils.stateLabel("impassable"), "Impassable");
assert.equal(AppUtils.stateLabel("passable"), "Passable");
// Every contract enum has display copy; nothing falls through to the raw value.
["pending", "in_progress", "resolved", "rejected"].forEach(v => {
  assert.notEqual(AppUtils.label("status", v), v);
  assert.notEqual(AppUtils.label("status_short", v), v);
});
["api", "model", "manual"].forEach(v => assert.notEqual(AppUtils.label("detection_mode", v), v));
["passable", "impassable", "unknown", "damaged", "not_damaged"].forEach(v => assert.notEqual(AppUtils.stateLabel(v), v));
assert.ok(!AppUtils.stateLabel("not_damaged").includes("_"));

/* --- workflow --- */
// Every status can reach every other; these are the moves worth a button.
assert.equal(AppUtils.nextStatuses("pending").sort().join(","), "in_progress,rejected");
assert.ok(AppUtils.nextStatuses("in_progress").includes("resolved"));
assert.ok(AppUtils.nextStatuses("in_progress").includes("pending"), "can be sent back to pending");
assert.ok(AppUtils.nextStatuses("resolved").includes("in_progress"), "closed work can be reopened");
assert.ok(AppUtils.nextStatuses("rejected").includes("pending"), "a dismissal can be undone");
assert.equal(AppUtils.nextStatuses("nonsense").length, 0);
// A lane never offers the lane it is already in.
["pending", "in_progress", "resolved", "rejected"].forEach(v =>
  assert.ok(!AppUtils.nextStatuses(v).includes(v), v + " does not offer itself"));

// A closed case does not hold a road shut -- the one rule the map, the pins and
// the headline figures share with app/pipeline.LIVE.
assert.equal(AppUtils.isLive({ status: "pending" }), true);
assert.equal(AppUtils.isLive({ status: "in_progress" }), true);
assert.equal(AppUtils.isLive({ status: "resolved" }), false);
assert.equal(AppUtils.isLive({ status: "rejected" }), false);
assert.equal(AppUtils.isLive(null), false);

// Reopening a closed case is not accepting a new one; a button that says
// "Accept — start work" on a resolved report is how a cleared road gets
// reopened by reflex.
["pending", "in_progress", "resolved", "rejected"].forEach(function (from) {
  AppUtils.nextStatuses(from).forEach(function (to) {
    const text = AppUtils.actionLabel(from, to);
    assert.ok(text && text.length, from + " -> " + to + " has a label");
    assert.ok(!/^Accept/.test(text) || from === "pending",
      "only a pending report is accepted, got " + JSON.stringify(text) + " on " + from);
  });
});
assert.equal(AppUtils.actionLabel("pending", "in_progress"), "Accept — start work");
assert.ok(/Reopen/.test(AppUtils.actionLabel("resolved", "in_progress")));
assert.ok(/Reopen/.test(AppUtils.actionLabel("resolved", "pending")));
assert.ok(/Reinstate/.test(AppUtils.actionLabel("rejected", "pending")));
// An unlisted move still renders as words, never "undefined".
assert.ok(!/undefined/.test(AppUtils.actionLabel("nonsense", "pending")));

assert.equal(AppUtils.statusClass("pending"), "warn");
assert.equal(AppUtils.statusClass("in_progress"), "ok");
assert.equal(AppUtils.statusClass("rejected"), "neutral");

/* --- priority bands: a column of zeroes is unreadable --- */
assert.equal(AppUtils.priorityBand(4068.6).label, "Critical");
assert.equal(AppUtils.priorityBand(2000).key, "critical");
assert.equal(AppUtils.priorityBand(964.9).key, "high");
assert.equal(AppUtils.priorityBand(499).key, "moderate");
assert.equal(AppUtils.priorityBand(3.9).key, "low");
// 0 is a real, measured "no access impact" -- not the same as unranked.
assert.equal(AppUtils.priorityBand(0).label, "No impact");
assert.equal(AppUtils.priorityBand(null).label, "Not ranked");
assert.notEqual(AppUtils.priorityBand(0).key, AppUtils.priorityBand(null).key);
// Bands never decrease as the score rises.
const order = ["none", "low", "moderate", "high", "critical"];
[0, 1, 49, 50, 499, 500, 1999, 2000, 9999].reduce(function (prev, score) {
  const rank = order.indexOf(AppUtils.priorityBand(score).key);
  assert.ok(rank >= prev, "band is monotonic at " + score);
  return rank;
}, 0);

/* --- relative time --- */
const ago = mins => new Date(Date.now() - mins * 60000).toISOString();
assert.equal(AppUtils.timeAgo(ago(0)), "just now");
assert.equal(AppUtils.timeAgo(ago(5)), "5 min ago");
assert.equal(AppUtils.timeAgo(ago(60)), "1 hour ago");
assert.equal(AppUtils.timeAgo(ago(180)), "3 hours ago");
assert.equal(AppUtils.timeAgo(ago(60 * 24)), "1 day ago");
assert.equal(AppUtils.timeAgo(ago(60 * 72)), "3 days ago");
assert.equal(AppUtils.timeAgo(null), "Unknown");
assert.equal(AppUtils.timeAgo("not a date"), "Unknown");

/* --- sorting --- */
assert.deepEqual(AppUtils.sortReports([{ priority_score: null }, { priority_score: 10 }, { priority_score: 20 }]).map(r => r.priority_score), [20, 10, null]);

/* --- population display rules --- */
assert.equal(AppUtils.populationText({ population: 100, population_source: "census" }), "100");
assert.equal(AppUtils.populationText({ population: 100, population_source: "raster" }), "est. 100");
assert.equal(AppUtils.populationText({ population: 0, population_source: "missing" }), "unknown");
// A null count is never rendered as zero, whatever the source claims.
assert.equal(AppUtils.populationText({ population: null, population_source: "census" }), "unknown");
assert.equal(AppUtils.populationText({ population: null, population_source: "raster" }), "unknown");
// Census and raster are never presented identically.
assert.notEqual(
  AppUtils.populationText({ population: 100, population_source: "census" }),
  AppUtils.populationText({ population: 100, population_source: "raster" })
);
assert.equal(AppUtils.label("population_source", "raster"), "Raster estimate");
assert.equal(AppUtils.label("population_source", "missing"), "No population data");
// The backend keeps raster estimates to one decimal; people are whole, so the
// display rounds. "est. 681.6 people cut off" is not a claim worth making.
assert.equal(AppUtils.populationText({ population: 681.6, population_source: "raster" }), "est. 682");
assert.equal(AppUtils.populationText({ population: 26.7, population_source: "raster" }), "est. 27");
assert.equal(AppUtils.populationText({ population: 17274.0, population_source: "census" }), "17,274");
const fractional = AppUtils.impactSummary([], [
  { id: "a", population: 681.6, population_source: "raster" },
  { id: "b", population: 26.7, population_source: "raster" }
]);
assert.equal(fractional.peopleAffected, 708, "no floating-point tail in the headline figure");
assert.equal(AppUtils.peopleText(fractional), "≈708");

/* --- impact summary --- */
const reports = [
  { id: 1, asset_type: "road", state: "impassable", status: "pending", edge_id: "edge-1" },
  { id: 2, asset_type: "road", state: "impassable", status: "resolved", edge_id: "edge-1" }, // same span
  { id: 3, asset_type: "road", state: "impassable", status: "resolved", edge_id: "edge-2" },
  { id: 4, asset_type: "road", state: "impassable", status: "rejected", edge_id: "edge-3" }, // dismissed
  { id: 5, asset_type: "road", state: "passable", status: "pending", edge_id: "edge-4" },
  { id: 6, asset_type: "building", state: "damaged", status: "pending", edge_id: null }
];
const pockets = [
  { id: "a", population: 8420, population_source: "census" },
  { id: "b", population: 3653, population_source: "raster" }
];
let summary = AppUtils.impactSummary(reports, pockets);
// Only edge-1's PENDING report counts. A resolved case means the road was
// cleared, so counting it here is what put "2 roads impassable" on the
// dashboard beside a single cut-off area.
assert.equal(summary.roadsImpassable, 1, "closed and dismissed excluded, buildings excluded");
assert.equal(summary.areasSevered, 2);
assert.equal(summary.peopleAffected, 12073);
assert.equal(summary.estimated, true);
assert.equal(summary.incomplete, false);
assert.equal(AppUtils.peopleText(summary), "≈12,073");

// Census only: no estimate marker.
summary = AppUtils.impactSummary(reports, [pockets[0]]);
assert.equal(summary.estimated, false);
assert.equal(AppUtils.peopleText(summary), "8,420");
assert.equal(AppUtils.populationCaveat(summary), "");

// A pocket with no population data is excluded and flagged, never counted as 0.
summary = AppUtils.impactSummary(reports, [pockets[0], { id: "c", population: null, population_source: "missing" }]);
assert.equal(summary.areasSevered, 2);
assert.equal(summary.peopleAffected, 8420);
assert.equal(summary.incomplete, true);
assert.ok(AppUtils.populationCaveat(summary).length > 0);

// Every pocket unmeasured: unknown, not zero.
summary = AppUtils.impactSummary(reports, [{ id: "c", population: null, population_source: "missing" }]);
assert.equal(summary.peopleAffected, null);
assert.equal(AppUtils.peopleText(summary), "unknown");

// No pockets at all: a real, measured zero.
summary = AppUtils.impactSummary(reports, []);
assert.equal(summary.areasSevered, 0);
assert.equal(summary.peopleAffected, 0);
assert.equal(AppUtils.peopleText(summary), "0");

// Reports with no bound span still count as separate cut roads.
assert.equal(AppUtils.impactSummary([
  { id: 7, asset_type: "road", state: "impassable", status: "pending", edge_id: null },
  { id: 8, asset_type: "road", state: "impassable", status: "pending", edge_id: null }
], []).roadsImpassable, 2);

// The headline can never claim more cut roads than the backend is blocking on:
// resolving the last live report on a span drops it out of the count.
const oneSpan = [{ id: 9, asset_type: "road", state: "impassable", status: "pending", edge_id: "edge-9" }];
assert.equal(AppUtils.impactSummary(oneSpan, [{ id: "p", population: 682, population_source: "raster" }]).roadsImpassable, 1);
assert.equal(AppUtils.impactSummary([Object.assign({}, oneSpan[0], { status: "resolved" })], []).roadsImpassable, 0);
assert.equal(AppUtils.impactSummary([Object.assign({}, oneSpan[0], { status: "in_progress" })], []).roadsImpassable, 1,
  "accepting a report does not clear the road");

/* --- mock fixtures back the demo --- */
const mock = context.window.MOCK_DATA;
assert.ok(mock.roads.length >= 300, "dense network, got " + mock.roads.length);
assert.ok(mock.roads.every(r => r.coordinates.length >= 2 && r.edge_id && r.highway));
assert.ok(new Set(mock.roads.map(r => r.highway)).size >= 3, "several highway classes");
assert.equal(new Set(mock.roads.map(r => r.edge_id)).size, mock.roads.length, "edge ids unique");
const demo = AppUtils.impactSummary(mock.reports, mock.pockets);
assert.equal(demo.roadsImpassable, 3);
assert.equal(demo.areasSevered, 2);
assert.equal(demo.peopleAffected, 12073);
assert.equal(demo.estimated, true);
assert.equal(demo.incomplete, false);

/* --- severing edges: contract shape and highlight targets --- */
// Arrays built inside the vm realm are not reference-equal to host arrays, so
// compare by value.
const sameList = (actual, expected, message) =>
  assert.equal(JSON.stringify(actual), JSON.stringify(expected), message);
const pocketA = mock.pockets.find(p => p.id === "pocket-a");
const pocketB = mock.pockets.find(p => p.id === "pocket-b");

// The map highlights these, so each one must carry drawable geometry.
mock.pockets.forEach(pocket => pocket.severing_edges.forEach(function (edge) {
  sameList(Object.keys(edge).sort(),
    ["bridge", "coordinates", "edge_id", "highway", "length_m", "name", "report_ids"],
    "severing edge matches CONTRACT.md");
  assert.ok(edge.coordinates.length >= 2, "highlight needs a drawable line");
  assert.equal(typeof edge.bridge, "boolean");
  assert.ok(Array.isArray(edge.report_ids));
}));

sameList(AppUtils.severingEdgeIds(pocketA), [pocketA.severing_edges[0].edge_id]);
assert.equal(AppUtils.severingEdgeIds(pocketB).length, 2, "two spans cut pocket-b together");
// A stale payload sending bare ids still yields something to highlight.
sameList(AppUtils.severingEdgeIds({ severing_edges: ["edge-1", "edge-2"] }), ["edge-1", "edge-2"]);
sameList(AppUtils.severingEdgeIds({}), []);
sameList(AppUtils.severingEdgeIds({ severing_edges: [null, { name: "no id" }] }), []);

// report_ids are stitched on from the live impassable reports, ascending, deduped.
const aReports = AppUtils.pocketReportIds(pocketA);
assert.ok(aReports.length >= 1, "the cut has a report behind it");
sameList(aReports, aReports.slice().sort((x, y) => x - y), "ascending");
sameList(AppUtils.pocketReportIds({
  severing_edges: [{ edge_id: "a", report_ids: [4, 1] }, { edge_id: "b", report_ids: [1, 9] }]
}), [1, 4, 9], "deduped across edges");
sameList(AppUtils.pocketReportIds({ severing_edges: [{ edge_id: "a" }] }), []);

/* --- measurement formatting --- */
assert.equal(AppUtils.kmText(4.65), "4.65 km");
assert.equal(AppUtils.kmText(null), "Not recorded");
assert.equal(AppUtils.metresText(507.4), "507 m");
assert.equal(AppUtils.metresText(null), "Not recorded");
assert.equal(AppUtils.areaText(1.842), "1.84 km²");
assert.equal(AppUtils.areaText(0.061), "0.061 km²", "small hulls keep a significant figure");
assert.equal(AppUtils.areaText(null), "Not recorded");

/* --- highway class copy --- */
assert.equal(AppUtils.highwayLabel("trunk"), "Trunk road");
assert.equal(AppUtils.highwayLabel("residential"), "Residential street");
// "unclassified" is a real OSM class (a minor public road), not an unknown one.
assert.equal(AppUtils.highwayLabel("unclassified"), "Minor road");
assert.notEqual(AppUtils.highwayLabel("unclassified"), "Unknown");
assert.equal(AppUtils.highwayLabel("trunk_link"), "Trunk ramp");
// The contract says tolerate any OSM value, so an unseen one must still read.
assert.equal(AppUtils.highwayLabel("busway"), "Busway");
assert.equal(AppUtils.highwayLabel("living_street"), "Living street");
assert.ok(!AppUtils.highwayLabel("some_new_class").includes("_"));
assert.equal(AppUtils.highwayLabel(null), "Unclassified");

/* --- report creation still lands in the queue --- */
context.window.API.createReport({ lat: 13.05, lon: 74.81, asset_type: "road", state: "impassable" }).then(report => {
  assert.equal(report.status, "pending");
  assert.equal(report.detection_mode, "manual");
  assert.equal(mock.reports[0].id, report.id);
  // The mock backend recomputes severance, so the demo has a state change to show.
  return context.window.API.getPockets().then(after => {
    assert.ok(after.length >= 3, "a new cut reveals a severed pocket");
  });
}).catch(error => { console.error(error); process.exit(1); });

const queue = new AppUtils.RetryQueue(async item => { if (item === "retry" && !queue.didRetry) { queue.didRetry = true; throw new Error("offline"); } });
queue.enqueue("retry");
setTimeout(() => {
  assert.equal(queue.size, 1);
  queue.flush().then(() => { assert.equal(queue.size, 0); console.log("PASS copy mapping, impact summary, population rules, road weights, workflow, bands, severing edges, fixtures, retry queue"); });
}, 0);
