"""One-shot: window-crop the national WorldPop raster to the AOI + 2 km buffer."""
import os
import sys

import rasterio
from rasterio.windows import from_bounds

BBOX = (74.78, 13.00, 74.86, 13.10)          # west, south, east, north
BUF = 0.02                                   # ~2 km in degrees at 13 N
BOUNDS = (BBOX[0] - BUF, BBOX[1] - BUF, BBOX[2] + BUF, BBOX[3] + BUF)

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
        "tiled": True,
    }
    with rasterio.open(dst_path, "w", **profile) as dst:
        dst.write(data)

with rasterio.open(dst_path) as chk:
    print(f"cropped: {chk.width}x{chk.height} cells, bounds={tuple(round(b,4) for b in chk.bounds)}, "
          f"crs={chk.crs}, {os.path.getsize(dst_path)/1e6:.2f} MB")
