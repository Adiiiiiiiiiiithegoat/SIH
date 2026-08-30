"""One-shot: window-crop the national WorldPop raster to the AOI + 2 km buffer."""
import os
import sys

import rasterio
from rasterio.windows import from_bounds

BBOX = (74.78, 13.00, 74.86, 13.10)          # west, south, east, north
# Must match the graph's buffer, not undercut it: the drive network is
# downloaded with a 3 km margin, and a 2 km raster left pockets near the edge
# with no population value at all.
BUFFER_KM = 3.0
_DEG_LAT = BUFFER_KM / 111.0                 # 0.027027
_DEG_LON = BUFFER_KM / (111.0 * 0.9744)      # 0.027737  (cos 13.05 deg)
BOUNDS = (BBOX[0] - _DEG_LON, BBOX[1] - _DEG_LAT,
          BBOX[2] + _DEG_LON, BBOX[3] + _DEG_LAT)

src_path, dst_path = sys.argv[1], sys.argv[2]

with rasterio.open(src_path) as src:
    win = from_bounds(*BOUNDS, transform=src.transform).round_offsets().round_lengths()
    if win.width <= 0 or win.height <= 0:
        sys.exit("AOI does not overlap the national raster -- check the bbox/CRS.")
    data = src.read(window=win)             # only the AOI window enters memory
    profile = src.profile | {
        "height": data.shape[1],
        "width": data.shape[2],
        "transform": src.window_transform(win),
        "compress": "deflate",
        # ponytail: the crop is a few hundred cells square -- untiled, and drop
        # the source's stripe block sizes, which aren't valid for this shape.
        "tiled": False,
        "bigtiff": "NO",
    }
    profile.pop("blockxsize", None)
    profile.pop("blockysize", None)
    with rasterio.open(dst_path, "w", **profile) as dst:
        dst.write(data)

with rasterio.open(dst_path) as chk:
    print(f"cropped: {chk.width}x{chk.height} cells, bounds={tuple(round(b,4) for b in chk.bounds)}, "
          f"crs={chk.crs}, {os.path.getsize(dst_path)/1e6:.2f} MB")
