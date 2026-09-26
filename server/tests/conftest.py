import base64
import io
import os
import sys

import pytest

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)


def _font(size):
    from PIL import ImageFont

    for name in ("arial.ttf", "DejaVuSans.ttf", "Arial.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_form_image(with_pii_text=False, masked_box=None):
    """PIL-drawn 1280x800 'web form': heading, 2 labelled inputs, 2 buttons.

    Returns (PIL image, layout dict of known boxes in pixels).
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1280, 800), "white")
    d = ImageDraw.Draw(img)
    layout = {}

    d.text((80, 40), "Scholarship Application", fill="black", font=_font(40))

    d.text((80, 140), "Full Name", fill="black", font=_font(22))
    layout["name_input"] = (80, 175, 500, 45)
    d.rectangle((80, 175, 580, 220), outline=(120, 120, 120), width=2, fill="white")

    d.text((80, 250), "Email Address", fill="black", font=_font(22))
    layout["email_input"] = (80, 285, 500, 45)
    d.rectangle((80, 285, 580, 330), outline=(120, 120, 120), width=2, fill="white")
    if with_pii_text:
        d.text((92, 293), "priya.sharma@example.com", fill="black", font=_font(22))

    layout["submit"] = (80, 380, 220, 55)
    d.rectangle((80, 380, 300, 435), fill=(37, 99, 235))
    d.text((115, 393), "Submit", fill="white", font=_font(26))

    layout["reset"] = (330, 380, 180, 55)
    d.rectangle((330, 380, 510, 435), fill=(229, 231, 235), outline=(150, 150, 150), width=2)
    d.text((375, 393), "Reset", fill="black", font=_font(26))

    d.text((80, 480), "Phone 9876543210 for help", fill="black", font=_font(22))

    if masked_box:
        x, y, w, h = masked_box
        d.rectangle((x, y, x + w, y + h), fill="black")
    return img, layout


def to_b64(img, fmt="JPEG"):
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


@pytest.fixture(scope="session")
def form_b64():
    img, layout = make_form_image()
    return to_b64(img), layout


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)
