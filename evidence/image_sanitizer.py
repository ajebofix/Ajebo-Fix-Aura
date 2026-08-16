"""Strict raster-image validation and metadata stripping for Wave 1.4.

The first intake slice accepts only JPEG, PNG and WebP. Raw uploaded bytes are
never written to object storage: Pillow must identify, fully decode and re-encode
the raster first. EXIF/XMP/ICC metadata is deliberately not carried forward.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_SANITIZED_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MAX_DIMENSION = 10_000

# Pillow warns above MAX_IMAGE_PIXELS and raises above twice the configured
# threshold. The warning is promoted to an exception inside sanitization too.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

_ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP")
_FORMAT_POLICY = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}


class EvidenceImageValidationError(ValueError):
    """Raised when uploaded bytes cannot become accepted raster evidence."""


@dataclass(frozen=True)
class SanitizedEvidenceImage:
    payload: bytes
    content_type: str
    extension: str
    byte_size: int
    sha256: str
    width: int
    height: int
    detected_format: str


def _read_bounded(stream) -> bytes:
    raw = stream.read(MAX_INPUT_BYTES + 1)
    if not raw:
        raise EvidenceImageValidationError("Select a non-empty image file.")
    if len(raw) > MAX_INPUT_BYTES:
        raise EvidenceImageValidationError("Image exceeds Aura's 2 MB intake limit.")
    return raw


def _open_verified(raw: bytes) -> tuple[str, Image.Image]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw), formats=_ALLOWED_FORMATS) as probe:
                detected_format = str(probe.format or "").upper()
                probe.verify()

            # ``verify`` invalidates the decoder state; reopen and force a full
            # pixel decode before accepting any bytes.
            image = Image.open(BytesIO(raw), formats=_ALLOWED_FORMATS)
            image.load()
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise EvidenceImageValidationError(
            "Aura could not safely decode this image."
        ) from exc

    if detected_format not in _FORMAT_POLICY:
        image.close()
        raise EvidenceImageValidationError("Only JPEG, PNG and WebP images are accepted.")

    if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
        image.close()
        raise EvidenceImageValidationError(
            "Animated or multi-frame images are not accepted in this intake."
        )

    width, height = image.size
    if width <= 0 or height <= 0:
        image.close()
        raise EvidenceImageValidationError("Image dimensions are invalid.")
    if width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_IMAGE_PIXELS:
        image.close()
        raise EvidenceImageValidationError("Image dimensions exceed Aura's safety limit.")

    return detected_format, image


def _normalise_pixels(image: Image.Image, detected_format: str) -> Image.Image:
    transposed = ImageOps.exif_transpose(image)

    if detected_format == "JPEG":
        return transposed.convert("RGB")

    has_alpha = transposed.mode in {"RGBA", "LA"} or (
        transposed.mode == "P" and "transparency" in transposed.info
    )
    return transposed.convert("RGBA" if has_alpha else "RGB")


def _encode_without_metadata(image: Image.Image, detected_format: str) -> bytes:
    output = BytesIO()

    if detected_format == "JPEG":
        image.save(
            output,
            format="JPEG",
            quality=90,
            optimize=True,
            exif=b"",
            icc_profile=None,
        )
    elif detected_format == "PNG":
        image.save(
            output,
            format="PNG",
            optimize=True,
            icc_profile=None,
            exif=b"",
        )
    else:
        image.save(
            output,
            format="WEBP",
            quality=90,
            method=4,
            exif=b"",
            icc_profile=None,
            xmp=b"",
        )

    payload = output.getvalue()
    if not payload or len(payload) > MAX_SANITIZED_BYTES:
        raise EvidenceImageValidationError(
            "The sanitized image exceeds Aura's safe storage limit."
        )
    return payload


def sanitize_evidence_image(
    stream,
    *,
    declared_content_type: str,
) -> SanitizedEvidenceImage:
    """Validate, decode and re-encode one untrusted raster upload."""

    raw = _read_bounded(stream)
    detected_format, image = _open_verified(raw)
    expected_content_type, extension = _FORMAT_POLICY[detected_format]

    declared = (declared_content_type or "").split(";", 1)[0].strip().lower()
    if declared != expected_content_type:
        image.close()
        raise EvidenceImageValidationError(
            "The uploaded file type does not match its image content."
        )

    try:
        sanitized_pixels = _normalise_pixels(image, detected_format)
        try:
            payload = _encode_without_metadata(sanitized_pixels, detected_format)
            width, height = sanitized_pixels.size
        finally:
            sanitized_pixels.close()
    finally:
        image.close()

    return SanitizedEvidenceImage(
        payload=payload,
        content_type=expected_content_type,
        extension=extension,
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        width=width,
        height=height,
        detected_format=detected_format,
    )
