from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw


@pytest.fixture
def sample_jpeg() -> bytes:
    image = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 100), "Synthetic Taxpayer", fill="black")
    draw.text((80, 160), "SSN 123-45-6789", fill="black")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=95)
    return output.getvalue()
