(function () {
  const U = window.AppUtils;
  const UU = window.UploadUtils;
  const bbox = window.MOCK_DATA.bbox;
  const home = [(bbox.south + bbox.north) / 2, (bbox.west + bbox.east) / 2];
  const el = id => document.getElementById(id);

  const map = L.map("locator", { zoomControl: false, attributionControl: false }).setView(home, 12);
  L.control.zoom({ position: "topright" }).addTo(map);
  L.control.attribution({ prefix: false, position: "bottomright" }).addTo(map);

  // Positron by default -- pale enough that the marker and the drawn road
  // network stay the thing your eye lands on. Satellite is one click away,
  // same toggle Google Maps uses, for when the imagery itself is the point.
  const B = U.BASEMAPS;
  const baseLayer = L.tileLayer(B.map.url, Object.assign({ attribution: B.map.attribution }, B.map.options)).addTo(map);
  const satLayer = L.tileLayer(B.satellite.url, Object.assign({ attribution: B.satellite.attribution }, B.satellite.options));
  L.control.layers({ "Map": baseLayer, "Satellite": satLayer }, null, { position: "topright", collapsed: false }).addTo(map);
  // Our drawn road network is thin light-gray lines -- built to be recessive
  // against a pale basemap, so it needs a white halo to stay visible once
  // that basemap is a busy satellite photo instead.
  map.on("baselayerchange", e => map.getContainer().classList.toggle("sat-mode", e.name === "Satellite"));

  // Our own marker, so nothing loads Leaflet's default PNGs.
  const icon = L.divIcon({
    className: "",
    html: '<div class="pin road" style="background:' + U.COLORS.unknown + ';color:' + U.COLORS.unknown + '"></div>',
    iconSize: [11, 11], iconAnchor: [6, 6]
  });
  const marker = L.marker(home, { draggable: true, icon: icon }).addTo(map);

  // The road network is the only geography here; without it this is a blank box.
  const netR = L.canvas({ padding: 0.3 }).addTo(map);
  API.getRoads().then(roads => roads.forEach(road => L.polyline(road.coordinates, {
    renderer: netR, interactive: false,
    color: U.COLORS.neutral, weight: U.roadWeight(road), opacity: 0.85
  }).addTo(map))).catch(() => { /* the locator still works without it */ });

  let file = null;

  function say(text, kind) { el("msg").textContent = text; el("msg").className = "msg " + kind; }
  function place(lat, lon) { marker.setLatLng([lat, lon]); map.setView([lat, lon], 15); }

  // A photo of damage from anywhere can be pinned to any real spot on this
  // network -- the photo and its location are independent inputs, so a
  // stock photo of a washed-out road works as well as one taken on site.
  map.on("click", function (e) {
    closeResults();
    place(e.latlng.lat, e.latlng.lng);
    say("Location set. Drag the marker if it is not quite right.", "ok");
  });

  function reset() {
    file = null;
    el("file").value = "";
    el("preview").style.display = "none";
    el("previewImg").removeAttribute("src");
    el("fileName").textContent = "";
    el("accuracy").value = "";
    el("place").value = "";
    closeResults();
    marker.setLatLng(home);
    map.setView(home, 12);
  }

  /* --------------------------------------------------------- place search */
  // Nominatim is a live call to a third party, unlike the rest of this page
  // -- if it fails or the device is offline, the search box just goes quiet
  // and the click/drag/EXIF paths above still work exactly as before.
  const results = el("placeResults"), input = el("place");
  let items = [], active = -1, controller = null;

  function closeResults() {
    // Without this, a slow response that outlives the user dismissing the
    // panel (click away, Escape, picking a result) would land afterwards
    // and silently reopen it with results nobody asked for any more.
    if (controller) { controller.abort(); controller = null; }
    results.classList.remove("on");
    results.innerHTML = "";
    items = [];
    active = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function renderStatus(text) {
    results.innerHTML = '<div class="status">' + U.escape(text) + "</div>";
    results.classList.add("on");
    input.setAttribute("aria-expanded", "true");
  }

  function highlight() {
    results.querySelectorAll("button").forEach(function (b, i) {
      b.classList.toggle("active", i === active);
      b.setAttribute("aria-selected", String(i === active));
      if (i === active) b.scrollIntoView({ block: "nearest" });
    });
    if (active >= 0) input.setAttribute("aria-activedescendant", "place-result-" + active);
    else input.removeAttribute("aria-activedescendant");
  }

  function choose(item) {
    const label = UU.placeLabel(item);
    place(Number(item.lat), Number(item.lon));
    input.value = label.name;
    closeResults();
    say("Set to " + label.name + ". Drag the marker if it is not quite right.", "ok");
  }

  function renderResults(list) {
    items = list;
    active = -1;
    if (!list.length) return renderStatus("No matches in the coverage area.");
    results.innerHTML = list.map(function (item, i) {
      const label = UU.placeLabel(item);
      return '<button type="button" id="place-result-' + i + '" role="option" aria-selected="false">' +
        '<span class="geosearch-name">' + U.escape(label.name) + "</span>" +
        (label.detail ? '<span class="geosearch-detail">' + U.escape(label.detail) + "</span>" : "") +
        "</button>";
    }).join("");
    results.classList.add("on");
    input.setAttribute("aria-expanded", "true");
    results.querySelectorAll("button").forEach((b, i) => b.addEventListener("click", () => choose(list[i])));
  }

  const search = UU.debounce(function (query) {
    if (controller) controller.abort();
    controller = new AbortController();
    renderStatus("Searching…");
    fetch(UU.geocodeUrl(bbox, query), { signal: controller.signal })
      .then(r => { if (!r.ok) throw new Error("geocoder " + r.status); return r.json(); })
      .then(renderResults)
      .catch(function (error) {
        if (error.name === "AbortError") return;      // superseded by a newer keystroke
        renderStatus("Search unavailable — drag the marker or click the map instead.");
      });
  }, 400);

  input.addEventListener("input", function () {
    const query = input.value.trim();
    if (query.length < 3) return closeResults();
    search(query);
  });

  input.addEventListener("keydown", function (e) {
    if (!items.length && e.key !== "Escape") return;
    if (e.key === "ArrowDown") { e.preventDefault(); active = (active + 1) % items.length; highlight(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = (active - 1 + items.length) % items.length; highlight(); }
    else if (e.key === "Enter") { e.preventDefault(); choose(items[active >= 0 ? active : 0]); }
    else if (e.key === "Escape") { closeResults(); }
  });

  document.addEventListener("click", function (e) {
    if (!el("geosearch").contains(e.target)) closeResults();
  });

  function readExif(f) {
    return new Promise(function (resolve) {
      if (!window.EXIF || !f || !f.type.startsWith("image/")) return resolve(null);
      EXIF.getData(f, function () {
        const la = window.UploadUtils.exifCoord(EXIF.getTag(this, "GPSLatitudeRef"), EXIF.getTag(this, "GPSLatitude"));
        const lo = window.UploadUtils.exifCoord(EXIF.getTag(this, "GPSLongitudeRef"), EXIF.getTag(this, "GPSLongitude"));
        const acc = EXIF.getTag(this, "GPSDOP") || EXIF.getTag(this, "GPSHPositioningError");
        resolve(la != null && lo != null ? { lat: la, lon: lo, accuracy: acc ? Number(acc) : null } : null);
      });
    });
  }

  async function locate() {
    const found = await readExif(file);
    if (found) {
      place(found.lat, found.lon);
      if (found.accuracy) el("accuracy").value = found.accuracy;
      return say("Location read from the photo. Drag the marker if it is not quite right.", "ok");
    }
    const noGps = () => say("No location in this file. Drag the marker to where the damage is.", "warn");
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        p => { place(p.coords.latitude, p.coords.longitude); say("Using this device's location. Drag the marker if needed.", "warn"); },
        noGps, { timeout: 5000 });
    } else noGps();
  }

  el("file").addEventListener("change", async function (e) {
    file = e.target.files[0];
    if (!file) return;
    el("fileName").textContent = file.name;
    el("preview").style.display = "block";
    el("previewImg").src = file.type.startsWith("image/")
      ? URL.createObjectURL(file) : "mocks/images/report-3.svg";
    await locate();
  });

  const drop = el("drop");
  drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", function (e) {
    e.preventDefault();
    drop.classList.remove("over");
    el("file").files = e.dataTransfer.files;
    el("file").dispatchEvent(new Event("change"));
  });

  async function send() {
    if (!file) return say("Choose a photo or video first.", "err");
    const point = marker.getLatLng();
    const form = new FormData();
    form.append("image", file);
    form.append("lat", point.lat);
    form.append("lon", point.lng);
    form.append("gps_accuracy_m", el("accuracy").value || "");
    form.append("asset_type", el("asset").value);
    try {
      const report = await API.createReport(form);
      reset();
      say("Report received. Reference #" + report.id + " is queued for review.", "ok");
    } catch (error) {
      say("Could not send yet. Keep this page open — it will retry when the connection returns.", "err");
      setTimeout(send, 4000);
    }
  }

  el("send").addEventListener("click", send);
  window.addEventListener("online", () => { if (file) send(); });
})();
