"""Generate the ResQueue PWA app icons — navy ground, amber 'RQ' mark,
matching the departures-board brand mark already used in the web header."""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "static")
NAVY = (13, 20, 32, 255)      # --paper (dark theme)
AMBER = (242, 185, 59, 255)   # --accent (dark theme)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\bahnschrift.ttf",
    r"C:\Windows\Fonts\seguisb.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
]


def load_font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def make_icon(size, out_name, corner_ratio=0.22):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = int(size * corner_ratio)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=NAVY)

    text = "RQ"
    font = load_font(int(size * 0.44))
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), text,
               font=font, fill=AMBER)

    img.save(os.path.join(OUT, out_name))
    print("wrote", out_name)


os.makedirs(OUT, exist_ok=True)
make_icon(192, "icon-192.png")
make_icon(512, "icon-512.png")
make_icon(180, "apple-touch-icon.png", corner_ratio=0.0)  # iOS masks its own corners
