(function () {
  const U = window.AppUtils;
  const bbox = window.MOCK_DATA.bbox;
  const home = [(bbox.south + bbox.north) / 2, (bbox.west + bbox.east) / 2];
  const el = id => document.getElementById(id);

  const map = L.map("locator", { zoomControl: false, attributionControl: false }).setView(home, 12);
  L.control.zoom({ position: "topright" }).addTo(map);

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

  function reset() {
    file = null;
    el("file").value = "";
    el("preview").style.display = "none";
    el("previewImg").removeAttribute("src");
    el("fileName").textContent = "";
    el("accuracy").value = "";
    marker.setLatLng(home);
    map.setView(home, 12);
  }

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
