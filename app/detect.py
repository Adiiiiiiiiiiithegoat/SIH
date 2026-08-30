"""The one seam where damage detection plugs in.

Nothing else in the backend knows how a state is decided. mode="api" calls
DeepSeek vision; no trained model exists, so there is no mode="model" -- an
API failure or a missing key falls back to the same "unknown" stub.

    detect(image_path, mode) -> {"state", "confidence", "asset_type",
                                 "detection_mode"}

`state` is contract-valid for the asset type: road -> passable / impassable /
unknown, building -> damaged / not_damaged / unknown. A manual report is not a
detection at all -- the operator asserted the state -- so mode="manual" echoes
what it was given at full confidence.
"""
import base64
import hashlib
import io
import json
import os

import httpx

from app import config

try:
    # Registers HEIF/HEIC with Pillow process-wide, so both the detector and
    # the EXIF reader in main.py can open an iPhone photo. Done at import so it
    # never depends on which of them happens to run first. Purely local decode
    # -- nothing here reaches the network.
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:      # pragma: no cover -- HEIC then degrades to "unknown"
    pass

ROAD_STATES = ("passable", "impassable", "unknown")
BUILDING_STATES = ("damaged", "not_damaged", "unknown")

_PROMPTS = {
    "road": (
        'Assess this citizen-submitted photo of a road after a disaster, for '
        'a rescue-priority system. Reply with strict JSON only, no markdown '
        'fences: {"state": "passable|impassable|unknown", "confidence": '
        '0.0-1.0, "reason": "one short sentence"}. "impassable" means a '
        'vehicle cannot physically get through (collapse, flooding, debris, '
        'washout, a large gap). "passable" means the road looks clear and '
        'driveable. Use "unknown" only if the photo genuinely does not show '
        'enough of the road to tell.'
    ),
    "building": (
        'Assess this citizen-submitted photo of a building after a disaster, '
        'for a rescue-priority system. Reply with strict JSON only, no '
        'markdown fences: {"state": "damaged|not_damaged|unknown", '
        '"confidence": 0.0-1.0, "reason": "one short sentence"}. "damaged" '
        'means visible structural damage (collapse, cracking, missing '
        'walls/roof, fire damage). Use "unknown" only if the photo genuinely '
        'does not show enough of the building to tell.'
    ),
}


def valid_states(asset_type):
    return BUILDING_STATES if asset_type == "building" else ROAD_STATES


def _cached(digest):
    path = os.path.join(config.DETECT_CACHE, f"{digest}.json")
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    return None


def _store_cache(digest, result):
    os.makedirs(config.DETECT_CACHE, exist_ok=True)
    with open(os.path.join(config.DETECT_CACHE, f"{digest}.json"), "w") as fh:
        json.dump(result, fh)


# What the vision endpoint will actually accept in a data: URL. Anything else
# is a 400, so it has to be transcoded before it is sent.
_API_FORMATS = {"jpeg", "png", "webp", "gif"}


def api_image(raw):
    """(bytes, format) the vision endpoint can read.

    The format is sniffed from the bytes, never from the filename: phones lie
    about extensions constantly, and a photo picked from an iPhone library can
    arrive as .heic, as a .jpg that is really HEIC, or with no extension at
    all. Declaring the wrong format is a 400 from the API, which used to fall
    through to "unknown" -- a silent loss of detection on exactly the photos
    this system exists to read.

    Anything the API cannot take is transcoded to JPEG. Raw HEIC needs
    pillow-heif (imported above); without it this raises and the caller
    degrades to "unknown", as before.
    """
    fmt = _sniff(raw)
    if fmt in _API_FORMATS:
        return raw, fmt

    from PIL import Image
    with Image.open(io.BytesIO(raw)) as im:
        im = im.convert("RGB")       # drops alpha and EXIF orientation quirks
        out = io.BytesIO()
        im.save(out, "JPEG", quality=88)
    return out.getvalue(), "jpeg"


def _sniff(raw):
    """The real image format, from its magic bytes. None if unrecognised."""
    if raw[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[4:8] == b"ftyp":
        return "heic"        # or any other ISO-BMFF still; both need transcoding
    return None


def _call_deepseek(raw, ext, asset_type):
    """One DeepSeek vision call. Raises on any failure -- the caller decides
    what an unusable response means."""
    b64 = base64.b64encode(raw).decode()
    resp = httpx.post(
        config.DEEPSEEK_URL,
        headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                 "Content-Type": "application/json"},
        # This model spends part of max_tokens on internal chain-of-thought
        # before it emits the JSON answer -- observed using all 200 on
        # reasoning and returning an empty completion. 900 leaves headroom for
        # both; a request-specific `reasoning_tokens` field would be cleaner
        # but DeepSeek's OpenAI-compatible endpoint doesn't expose one here.
        json={"model": config.DEEPSEEK_MODEL, "max_tokens": 900,
              "messages": [{"role": "user", "content": [
                  {"type": "text", "text": _PROMPTS[asset_type]},
                  {"type": "image_url",
                   "image_url": {"url": f"data:image/{ext};base64,{b64}"}},
              ]}]},
        timeout=20.0,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(content)


def _detect_api(image_path, asset_type, allowed):
    full = os.path.join(config.UPLOADS, os.path.basename(image_path))
    with open(full, "rb") as fh:
        raw = fh.read()
    digest = hashlib.sha1(raw).hexdigest()

    result = _cached(digest)
    if result is None:
        # Sniff and, if need be, transcode before the call -- the cache is keyed
        # on the ORIGINAL bytes, so a re-upload of the same photo still hits.
        payload, fmt = api_image(raw)
        result = _call_deepseek(payload, fmt, asset_type)
        _store_cache(digest, result)

    state = result.get("state")
    if state not in allowed:
        state = "unknown"
    confidence = max(0.0, min(1.0, float(result.get("confidence", config.STUB_CONFIDENCE))))
    return {"state": state, "confidence": confidence,
            "asset_type": asset_type, "detection_mode": "api"}


def detect(image_path, mode=None, asset_type="road", state=None):
    """Classify one image. Returns the contract fields, never raises on a
    missing or unreadable file, or a failed API call -- an unusable image is
    an "unknown", which the priority scorer already handles via
    UNKNOWN_BELIEF.

    `state` is only honoured for mode="manual", where the operator is the
    detector. The model and api paths ignore it.
    """
    mode = mode or config.DETECTION_MODE
    allowed = valid_states(asset_type)

    if mode == "manual":
        return {"state": state if state in allowed else "unknown",
                "confidence": 1.0, "asset_type": asset_type,
                "detection_mode": "manual"}

    if mode == "api" and image_path and config.DEEPSEEK_API_KEY:
        try:
            return _detect_api(image_path, asset_type, allowed)
        except Exception:
            pass  # network/parse failure -- an unusable image is "unknown"

    # ponytail: no key, or the call above failed -- deliberately constant stub.
    return {"state": "unknown", "confidence": config.STUB_CONFIDENCE,
            "asset_type": asset_type, "detection_mode": "api"}
