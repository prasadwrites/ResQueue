"""Generate Android launcher icons (all densities) from the same RQ mark
used for the PWA icons, for the android-twa/ project."""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "android-twa", "app", "src", "main", "res")
NAVY = (13, 20, 32, 255)
AMBER = (242, 185, 59, 255)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\bahnschrift.ttf",
    r"C:\Windows\Fonts\seguisb.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
]

# density -> (launcher px, foreground-layer px for adaptive icons)
DENSITIES = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}


def load_font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def make_square(size, corner_ratio):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = int(size * corner_ratio)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=NAVY)
    font = load_font(int(size * 0.42))
    text = "RQ"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), text, font=font, fill=AMBER)
    return img


for folder, size in DENSITIES.items():
    d = os.path.join(OUT, folder)
    os.makedirs(d, exist_ok=True)
    make_square(size, 0.22).save(os.path.join(d, "ic_launcher.png"))
    make_square(size, 0.5).save(os.path.join(d, "ic_launcher_round.png"))
    print("wrote", folder)

# Play Store listing icon (512x512, no transparency requirements beyond square)
store_dir = os.path.join(HERE, "..", "android-twa", "store-assets")
os.makedirs(store_dir, exist_ok=True)
make_square(512, 0.0).convert("RGB").save(os.path.join(store_dir, "play-store-icon-512.png"))
print("wrote store-assets/play-store-icon-512.png")
