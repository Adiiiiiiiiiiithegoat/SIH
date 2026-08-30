"""What reaches the vision endpoint, whatever the phone sends.

The format has to be sniffed from the bytes rather than read off the filename.
An iPhone photo picked from the library can arrive as .heic, as a .jpg that is
really something else, or with no extension at all, and declaring the wrong
format is a 400 from the API -- which degrades to "unknown" and silently loses
detection on exactly the photos this system exists to read.
"""
import io

import pytest
from PIL import Image

from app import detect


def raster(fmt, size=(64, 48), colour=(120, 100, 80)):
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, fmt)
    return buf.getvalue()


@pytest.mark.parametrize("fmt,expected", [("JPEG", "jpeg"), ("PNG", "png"),
                                          ("GIF", "gif"), ("WEBP", "webp")])
def test_formats_the_api_accepts_are_passed_through_untouched(fmt, expected):
    """No needless re-encode: a jpeg the API can already read is sent as-is."""
    raw = raster(fmt)
    out, declared = detect.api_image(raw)
    assert declared == expected
    assert out is raw, "a supported format must not be transcoded"


def test_the_declared_format_comes_from_the_bytes_not_the_name():
    """A phone naming a PNG 'photo.jpg' must not have it declared as jpeg."""
    png = raster("PNG")
    _, declared = detect.api_image(png)
    assert declared == "png"


def test_a_heic_photo_is_transcoded_to_jpeg():
    """iPhone's default format. The endpoint 400s on it, so it must be
    converted before the call, not after the failure."""
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (90, 80, 70)).save(buf, format="HEIF")
    heic = buf.getvalue()

    assert detect._sniff(heic) == "heic", "sniffed from the ftyp box"
    out, declared = detect.api_image(heic)
    assert declared == "jpeg"
    assert out[:3] == b"\xff\xd8\xff", "really a jpeg now, not a relabelled heic"
    assert Image.open(io.BytesIO(out)).size == (64, 48)


def test_something_that_is_not_an_image_raises_so_the_caller_degrades():
    """detect() catches this and returns 'unknown' -- an unreadable photo must
    never take a report down with it."""
    with pytest.raises(Exception):
        detect.api_image(b"this is not an image at all")

    # ... and that is exactly what the caller does with it.
    assert detect.detect("nope.jpg", mode="api")["state"] == "unknown"


def test_sniff_recognises_nothing_as_none():
    assert detect._sniff(b"\x00\x01\x02\x03") is None
