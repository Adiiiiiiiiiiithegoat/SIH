(function () {
  const bbox = window.MOCK_DATA.bbox;
  const map = L.map("mapSmall", { zoomControl: false }).setView([(bbox.south + bbox.north) / 2, (bbox.west + bbox.east) / 2], 12);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  let marker = L.marker(map.getCenter(), { draggable: true }).addTo(map);
  let selectedFile = null;
  let exifLocation = null;
  const input = document.getElementById("mediaInput");
  const preview = document.getElementById("preview");
  const previewImage = document.getElementById("previewImage");
  const notice = document.getElementById("notice");
  const queue = document.getElementById("queue");
  const accuracy = document.getElementById("accuracy");

  function setNotice(message, type) { notice.textContent = message; notice.className = "notice " + type; }
  function setLocation(lat, lon) { marker.setLatLng([lat, lon]); map.setView([lat, lon], 14); }
  const exifCoord = window.UploadUtils.exifCoord;
  function readExif(file) {
    return new Promise(resolve => {
      if (!window.EXIF || !file || !file.type.startsWith("image/")) return resolve(null);
      EXIF.getData(file, function () {
        const lat = exifCoord(EXIF.getTag(this, "GPSLatitudeRef"), EXIF.getTag(this, "GPSLatitude"));
        const lon = exifCoord(EXIF.getTag(this, "GPSLongitudeRef"), EXIF.getTag(this, "GPSLongitude"));
        const acc = EXIF.getTag(this, "GPSDOP") || EXIF.getTag(this, "GPSHPositioningError");
        resolve(lat != null && lon != null ? { lat, lon, accuracy: acc ? Number(acc) : null } : null);
      });
    });
  }
  async function locate() {
    exifLocation = await readExif(selectedFile);
    if (exifLocation) {
      setLocation(exifLocation.lat, exifLocation.lon);
      if (exifLocation.accuracy) accuracy.value = exifLocation.accuracy;
      setNotice("Location read from photo metadata. Drag the pin if needed.", "success");
      return;
    }
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(pos => setLocation(pos.coords.latitude, pos.coords.longitude), () => setNotice("No GPS in this file. Drag the pin to the report location.", "success"), { timeout: 5000 });
    } else setNotice("No GPS in this file. Drag the pin to the report location.", "success");
  }
  input.addEventListener("change", async event => {
    selectedFile = event.target.files[0];
    if (!selectedFile) return;
    document.getElementById("fileName").textContent = selectedFile.name;
    preview.style.display = "block";
    if (selectedFile.type.startsWith("image/")) previewImage.src = URL.createObjectURL(selectedFile);
    else previewImage.src = "mocks/images/report-3.svg";
    await locate();
  });
  document.getElementById("dropzone").addEventListener("dragover", event => { event.preventDefault(); event.currentTarget.classList.add("drag"); });
  document.getElementById("dropzone").addEventListener("dragleave", event => event.currentTarget.classList.remove("drag"));
  document.getElementById("dropzone").addEventListener("drop", event => { event.preventDefault(); event.currentTarget.classList.remove("drag"); input.files = event.dataTransfer.files; input.dispatchEvent(new Event("change")); });
  async function submit() {
    if (!selectedFile) return setNotice("Choose a photo or video before submitting.", "error");
    const point = marker.getLatLng();
    const data = new FormData();
    data.append("image", selectedFile);
    data.append("lat", point.lat);
    data.append("lon", point.lng);
    data.append("gps_accuracy_m", accuracy.value || "");
    data.append("asset_type", document.getElementById("assetType").value);
    try {
      const report = await API.createReport(data);
      queue.classList.remove("show");
      setNotice("Report received. Reference #" + report.id + " has been queued for review.", "success");
      selectedFile = null; input.value = "";
    } catch (error) {
      queue.classList.add("show");
      setNotice("Could not send the report yet. Keep this page open and we’ll retry automatically.", "error");
      setTimeout(submit, 4000);
    }
  }
  document.getElementById("submitReport").addEventListener("click", submit);
  window.addEventListener("online", () => { if (selectedFile) submit(); });
})();
