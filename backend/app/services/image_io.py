"""Upload normalisation (requirement 5.1).

Real phone galleries are messy: the three sample photos shipped with this
project alone include a ``.jfif`` and a 1.8 MB ``.avif``. Everything is
funnelled through one path that produces a plain sRGB JPEG, correctly rotated,
at a bounded size -- so no stage downstream has to care about input format.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import settings

_OPENERS_REGISTERED = False

#: Extensions we accept. AVIF needs Pillow >= 11.3; HEIC needs pillow-heif.
ACCEPTED_SUFFIXES = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp",
    ".avif", ".heic", ".heif", ".bmp", ".tif", ".tiff",
}


def register_openers() -> None:
    """Enable HEIC/HEIF decoding. Safe to call repeatedly."""
    global _OPENERS_REGISTERED
    if _OPENERS_REGISTERED:
        return
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except Exception:  # pragma: no cover - optional codec
        pass
    _OPENERS_REGISTERED = True


class UnsupportedImage(ValueError):
    """Raised when the bytes cannot be decoded as an image at all."""


@dataclass
class WorkingImages:
    """The two derivatives every project keeps."""

    original_path: Path
    display_path: Path
    width: int
    height: int
    display_width: int
    display_height: int
    source_format: str
    source_mode: str


def decode(data: bytes) -> tuple[Image.Image, str, str]:
    """Bytes -> upright RGB image, plus the detected format and colour mode."""
    register_openers()
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise UnsupportedImage(
            "That file could not be read as an image. Please upload a JPG, PNG, "
            "WEBP, AVIF or HEIC photo."
        ) from exc

    source_format = (img.format or "unknown").lower()
    source_mode = img.mode

    # Honour the EXIF orientation flag before anything measures the pixels --
    # a sideways photo would wreck both detection and area estimation.
    img = ImageOps.exif_transpose(img)

    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.split()[-1])
        img = flat
    elif img.mode != "RGB":
        img = img.convert("RGB")

    return img, source_format, source_mode


def _fit(img: Image.Image, max_edge: int) -> Image.Image:
    w, h = img.size
    longest = max(w, h)
    if longest <= max_edge:
        return img
    scale = max_edge / float(longest)
    return img.resize(
        (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS
    )


def build_working_set(data: bytes, dest_dir: Path) -> WorkingImages:
    """Decode an upload and write the working + display JPEGs."""
    img, source_format, source_mode = decode(data)
    dest_dir.mkdir(parents=True, exist_ok=True)

    working = _fit(img, settings.max_image_edge)
    display = _fit(working, settings.display_edge)

    original_path = dest_dir / "original.jpg"
    display_path = dest_dir / "display.jpg"
    working.save(original_path, "JPEG", quality=92, subsampling=1, optimize=True)
    display.save(display_path, "JPEG", quality=88, optimize=True)

    return WorkingImages(
        original_path=original_path,
        display_path=display_path,
        width=working.width,
        height=working.height,
        display_width=display.width,
        display_height=display.height,
        source_format=source_format,
        source_mode=source_mode,
    )


# ---------------------------------------------------------------------------
# numpy / OpenCV interop
# ---------------------------------------------------------------------------


def to_bgr(img: Image.Image) -> np.ndarray:
    """PIL RGB -> OpenCV BGR uint8 array."""
    return np.asarray(img.convert("RGB"), dtype=np.uint8)[:, :, ::-1].copy()


def from_bgr(arr: np.ndarray) -> Image.Image:
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr[:, :, ::-1])


def make_thumbnail(src: Path, dest: Path, max_edge: int = 1200, quality: int = 85) -> Path:
    """Downscaled copy, used for the UI and for embedding in the PDF.

    Without this the report embeds full-resolution JPEGs and balloons to
    several megabytes for no visible benefit at A4.
    """
    register_openers()
    with Image.open(src) as img:
        small = _fit(img.convert("RGB"), max_edge)
        dest.parent.mkdir(parents=True, exist_ok=True)
        small.save(dest, "JPEG", quality=quality, optimize=True)
    return dest


def read_bgr(path: Path) -> np.ndarray:
    register_openers()
    with Image.open(path) as img:
        return to_bgr(img)


def save_bgr(arr: np.ndarray, path: Path, quality: int = 90) -> tuple[int, int]:
    img = from_bgr(arr)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=quality, subsampling=1, optimize=True)
    return img.size
