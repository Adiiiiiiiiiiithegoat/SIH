"""FastAPI app. Endpoints exactly as CONTRACT.md defines them, plus the two
map layers the dashboard needs: /api/roads and /api/pockets.

Runs fully offline: the graph, raster, settlements and the frozen OSM reference
layer all live under app/data/.

    uvicorn app.main:app --reload
"""
import datetime
import os
import shutil
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import binding, config, detect, pipeline, severance, store
from app.network import net

app = FastAPI(title="Disaster infrastructure assessment", version="1.0")

# The frontend is opened from the filesystem or its own dev server, so the
# browser calls this from a different origin.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

os.makedirs(config.UPLOADS, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=config.UPLOADS), name="uploads")


@app.on_event("startup")
def _warm():
    """Load the graph and the baseline distance map before the first request,
    so a user never pays the 4 s cold start."""
    net()
    pipeline.recompute()


# --- helpers --------------------------------------------------------------
def _f(value):
    """Form values arrive as strings and empty means absent, not zero."""
    if value is None or value == "" or value == "null":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _payload(request, form_fields):
    """Accept either a multipart form (the citizen upload page) or a JSON body
    (the operator's map click). Same record either way."""
    if form_fields:
        return form_fields
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _public(report, request):
    """Rewrite a stored upload path into a URL the browser can actually fetch.

    The dashboard puts image_path straight into <img src>, which the browser
    resolves against the PAGE's origin -- not the API's. Same-origin that is
    harmless, but with the frontend on its own dev server every thumbnail 404s.
    Only our own /uploads/ paths are rewritten; a path the caller supplied
    (a manual report pointing at a frontend asset) is left exactly as given.
    """
    if report and str(report.get("image_path") or "").startswith("/uploads/"):
        report = dict(report, image_path=str(request.base_url).rstrip("/") + report["image_path"])
    return report


def _save_image(upload):
    if upload is None or not getattr(upload, "filename", ""):
        return None
    ext = os.path.splitext(upload.filename)[1][:10] or ".bin"
    name = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(config.UPLOADS, name), "wb") as fh:
        shutil.copyfileobj(upload.file, fh)
    return f"/uploads/{name}"


def _exif_bearing(path):
    """GPSImgDirection from the photo, if the camera recorded one.

    A photographer standing in front of a blocked road faces across it, so this
    lets the binder prefer the edge perpendicular to the lens over the one the
    photographer was standing on.
    """
    if not path:
        return None
    full = os.path.join(config.UPLOADS, os.path.basename(path))
    try:
        from PIL import Image, ExifTags
        with Image.open(full) as im:
            gps = (im.getexif().get_ifd(ExifTags.IFD.GPSInfo) or {})
        value = gps.get(ExifTags.GPS.GPSImgDirection)
        return None if value is None else float(value) % 360.0
    except Exception:
        return None      # not an image, no EXIF, or no direction tag -- fine


# --- reports --------------------------------------------------------------
@app.post("/api/reports")
async def create_report(
    request: Request,
    image: UploadFile = File(None),
    lat: str = Form(None),
    lon: str = Form(None),
    gps_accuracy_m: str = Form(None),
    asset_type: str = Form(None),
    state: str = Form(None),
    bearing: str = Form(None),
):
    form = {k: v for k, v in
            {"lat": lat, "lon": lon, "gps_accuracy_m": gps_accuracy_m,
             "asset_type": asset_type, "state": state, "bearing": bearing}.items()
            if v is not None}
    data = await _payload(request, form)

    lat_v, lon_v = _f(data.get("lat")), _f(data.get("lon"))
    if lat_v is None or lon_v is None:
        raise HTTPException(422, "lat and lon are required")

    asset = data.get("asset_type") or "road"
    if asset not in ("road", "building"):
        raise HTTPException(422, "asset_type must be 'road' or 'building'")

    image_path = _save_image(image) or data.get("image_path")
    accuracy = _f(data.get("gps_accuracy_m"))

    # An operator clicking the map is asserting the state; a photo is not.
    mode = "manual" if image is None or not image.filename else config.DETECTION_MODE
    result = detect.detect(image_path, mode, asset, data.get("state"))

    bearing_v = _f(data.get("bearing"))
    if bearing_v is None:
        bearing_v = _exif_bearing(image_path)

    edge, candidates = (None, [])
    if asset == "road":
        edge, candidates = binding.bind(lat_v, lon_v, accuracy, bearing_v)

    report_id = store.insert(
        image_path=image_path, lat=lat_v, lon=lon_v, gps_accuracy_m=accuracy,
        asset_type=asset, state=result["state"], confidence=result["confidence"],
        edge_id=edge, n_reports=1, status="pending",
        detection_mode=result["detection_mode"], priority_score=None,
        priority_reason=None,
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        bearing=bearing_v, bind_candidates=candidates)

    pipeline.recompute()
    return _public(store.get(report_id), request)


@app.get("/api/reports")
def list_reports(request: Request, status: str = None):
    return [_public(r, request) for r in store.all_reports(status)]


@app.get("/api/reports/{report_id}")
def get_report(report_id: int, request: Request):
    report = store.get(report_id, full=True)
    if report is None:
        raise HTTPException(404, "no such report")
    return _public(report, request)


@app.post("/api/reports/{report_id}/status")
async def set_status(report_id: int, request: Request):
    body = await request.json()
    status = body.get("status")
    if status not in ("pending", "resolved", "rejected"):
        raise HTTPException(422, "status must be pending, resolved or rejected")
    if store.get(report_id) is None:
        raise HTTPException(404, "no such report")
    store.update(report_id, status=status)
    pipeline.recompute()
    return _public(store.get(report_id), request)


@app.post("/api/reports/{report_id}/edge")
async def override_edge(report_id: int, request: Request):
    """Operator override of a binding, chosen from `bind_candidates`.

    Any edge in the graph is accepted, not only the shortlist -- the operator
    can see things the GPS cannot.
    """
    report = store.get(report_id, full=True)
    if report is None:
        raise HTTPException(404, "no such report")
    body = await request.json()
    edge = body.get("edge_id")
    if edge is not None and edge not in net().spans:
        raise HTTPException(422, f"no edge {edge!r} in the road network")
    store.update(report_id, edge_id=edge)
    pipeline.recompute()
    return _public(store.get(report_id, full=True), request)


# --- map layers -----------------------------------------------------------
@app.get("/api/roads")
def roads():
    return pipeline.roads()


@app.get("/api/pockets")
def pockets():
    """Currently severed pockets: polygon, population and what they lost."""
    return pipeline.current()["pockets"]


@app.get("/api/detours")
def detours():
    return pipeline.current()["detours"]


# --- settings -------------------------------------------------------------
@app.get("/api/settings")
def get_settings():
    """Every live-adjustable constant, so the dashboard can show what the
    scores were computed with."""
    return {"detection_mode": config.DETECTION_MODE,
            **{k.lower(): getattr(config, k) for k in config.MUTABLE
               if k != "DETECTION_MODE"}}


@app.post("/api/settings")
async def set_settings(request: Request):
    body = await request.json()
    if "detection_mode" in body:
        mode = body["detection_mode"]
        if mode not in ("api", "model", "manual"):
            raise HTTPException(422, "detection_mode must be api, model or manual")
        config.DETECTION_MODE = mode
    for key in config.MUTABLE:
        if key == "DETECTION_MODE" or key.lower() not in body:
            continue
        try:
            setattr(config, key, type(getattr(config, key))(body[key.lower()]))
        except (TypeError, ValueError):
            raise HTTPException(422, f"{key.lower()} must be a number")
    # The severance filters are memoised per (edge, blockages); a changed filter
    # invalidates every cached answer.
    severance.hypothetical.cache_clear()
    pipeline.recompute()
    return get_settings()
