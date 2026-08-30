"""The one seam where damage detection plugs in.

Nothing else in the backend knows how a state is decided. Today this returns a
placeholder; tomorrow `mode="model"` loads local weights and `mode="api"` calls
a remote classifier, and no caller changes.

    detect(image_path, mode) -> {"state", "confidence", "asset_type",
                                 "detection_mode"}

`state` is contract-valid for the asset type: road -> passable / impassable /
unknown, building -> damaged / not_damaged / unknown. A manual report is not a
detection at all -- the operator asserted the state -- so mode="manual" echoes
what it was given at full confidence.
"""
from app import config

ROAD_STATES = ("passable", "impassable", "unknown")
BUILDING_STATES = ("damaged", "not_damaged", "unknown")


def valid_states(asset_type):
    return BUILDING_STATES if asset_type == "building" else ROAD_STATES


def detect(image_path, mode=None, asset_type="road", state=None):
    """Classify one image. Returns the contract fields, never raises on a
    missing or unreadable file -- an unusable image is an "unknown", which the
    priority scorer already handles via UNKNOWN_BELIEF.

    `state` is only honoured for mode="manual", where the operator is the
    detector. The model and api paths will ignore it once they are real.
    """
    mode = mode or config.DETECTION_MODE
    allowed = valid_states(asset_type)

    if mode == "manual":
        return {"state": state if state in allowed else "unknown",
                "confidence": 1.0, "asset_type": asset_type,
                "detection_mode": "manual"}

    # ponytail: placeholder until real detection lands. Deliberately constant --
    # a random stub would make the queue reorder for no reason between runs and
    # hide real bugs in the severance/priority path behind noise.
    return {"state": "unknown", "confidence": config.STUB_CONFIDENCE,
            "asset_type": asset_type,
            "detection_mode": mode if mode in ("api", "model") else "model"}
