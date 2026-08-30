"""The one seam where damage detection plugs in.

Nothing else in the backend knows how a state is decided. mode="model" is
still a placeholder (no trained model exists); mode="api" calls DeepSeek
vision and no caller changes.

    detect(image_path, mode) -> {"state", "confidence", "asset_type",
                                 "detection_mode"}

`state` is contract-valid for the asset type: road -> passable / impassable /
unknown, building -> damaged / not_damaged / unknown. A manual report is not a
detection at all -- the operator asserted the state -- so mode="manual" echoes
what it was given at full confidence.
"""
import base64
import hashlib
import json
import os

import httpx

from app import config

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


def _call_deepseek(raw, ext, asset_type):
    """One DeepSeek vision call. Raises on any failure -- the caller decides
    what an unusable response means."""
    b64 = base64.b64encode(raw).decode()
    if ext in ("jpg", ""):
        ext = "jpeg"
    resp = httpx.post(
        config.DEEPSEEK_URL,
        headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                 "Content-Type": "application/json"},
        json={"model": config.DEEPSEEK_MODEL, "max_tokens": 200,
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

    cached = _cached(digest)
    result = cached or _call_deepseek(raw, os.path.splitext(full)[1][1:].lower(), asset_type)
    if cached is None:
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

    # ponytail: placeholder for mode="model" (no trained model exists) and any
    # api failure -- deliberately constant, see module docstring.
    return {"state": "unknown", "confidence": config.STUB_CONFIDENCE,
            "asset_type": asset_type,
            "detection_mode": mode if mode in ("api", "model") else "model"}
