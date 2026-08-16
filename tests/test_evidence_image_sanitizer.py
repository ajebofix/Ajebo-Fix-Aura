from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from evidence.image_sanitizer import (
    MAX_INPUT_BYTES,
    EvidenceImageValidationError,
    sanitize_evidence_image,
)


def _image_bytes(
    image_format: str,
    *,
    with_metadata: bool = False,
) -> bytes:
    image = Image.new("RGB", (48, 32), (24, 96, 160))
    output = BytesIO()

    kwargs: dict[str, object] = {}
    if with_metadata and image_format == "JPEG":
        exif = Image.Exif()
        exif[0x010E] = "sensitive test description"
        exif[0x013B] = "private test author"
        kwargs["exif"] = exif

    image.save(output, format=image_format, **kwargs)
    image.close()
    return output.getvalue()


@pytest.mark.parametrize(
    ("image_format", "content_type", "expected_extension"),
    [
        ("JPEG", "image/jpeg", ".jpg"),
        ("PNG", "image/png", ".png"),
        ("WEBP", "image/webp", ".webp"),
    ],
)
def test_valid_raster_is_fully_decoded_and_reencoded(
    image_format: str,
    content_type: str,
    expected_extension: str,
):
    raw = _image_bytes(image_format)

    sanitized = sanitize_evidence_image(
        BytesIO(raw),
        declared_content_type=content_type,
    )

    assert sanitized.content_type == content_type
    assert sanitized.extension == expected_extension
    assert sanitized.width == 48
    assert sanitized.height == 32
    assert sanitized.byte_size == len(sanitized.payload)
    assert len(sanitized.sha256) == 64

    with Image.open(BytesIO(sanitized.payload)) as decoded:
        decoded.load()
        assert decoded.size == (48, 32)
        assert str(decoded.format).upper() == image_format


def test_jpeg_metadata_is_not_carried_into_sanitized_output():
    raw = _image_bytes("JPEG", with_metadata=True)
    with Image.open(BytesIO(raw)) as original:
        assert original.getexif().get(0x010E) == "sensitive test description"

    sanitized = sanitize_evidence_image(
        BytesIO(raw),
        declared_content_type="image/jpeg",
    )

    assert sanitized.payload != raw
    with Image.open(BytesIO(sanitized.payload)) as cleaned:
        cleaned.load()
        assert len(cleaned.getexif()) == 0
        assert not cleaned.info.get("exif")
        assert not cleaned.info.get("xmp")
        assert not cleaned.info.get("icc_profile")


def test_declared_mime_must_match_decoded_raster():
    raw_png = _image_bytes("PNG")

    with pytest.raises(EvidenceImageValidationError, match="does not match"):
        sanitize_evidence_image(
            BytesIO(raw_png),
            declared_content_type="image/jpeg",
        )


def test_svg_and_gif_are_rejected_by_raster_allowlist():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    with pytest.raises(EvidenceImageValidationError):
        sanitize_evidence_image(
            BytesIO(svg),
            declared_content_type="image/svg+xml",
        )

    gif_image = Image.new("P", (10, 10))
    gif_buffer = BytesIO()
    gif_image.save(gif_buffer, format="GIF")
    gif_image.close()

    with pytest.raises(EvidenceImageValidationError):
        sanitize_evidence_image(
            BytesIO(gif_buffer.getvalue()),
            declared_content_type="image/gif",
        )


def test_malformed_image_is_rejected():
    with pytest.raises(EvidenceImageValidationError):
        sanitize_evidence_image(
            BytesIO(b"this is not an image"),
            declared_content_type="image/jpeg",
        )


def test_raw_input_is_bounded_before_image_decode():
    oversized = BytesIO(b"x" * (MAX_INPUT_BYTES + 1))

    with pytest.raises(EvidenceImageValidationError, match="2 MB"):
        sanitize_evidence_image(
            oversized,
            declared_content_type="image/jpeg",
        )
