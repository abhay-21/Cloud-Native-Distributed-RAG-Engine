#!/usr/bin/env python3
"""
generate_lpu_logo.py
Creates a professional LPU logo placeholder using PIL/Pillow.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent.parent / "report" / "lpu_logo.png"
W, H = 600, 200

img = Image.new("RGB", (W, H), (255, 255, 255))
d = ImageDraw.Draw(img)

# Background gradient-like block
d.rectangle([0, 0, W, H], fill=(1, 58, 128))  # LPU dark blue

# Gold accent strip
d.rectangle([0, H-20, W, H], fill=(255, 180, 0))

# White text
try:
    big   = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 36)
    small = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 16)
    tiny  = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 13)
except Exception:
    big = small = tiny = ImageFont.load_default()

line1 = "LOVELY PROFESSIONAL UNIVERSITY"
line2 = "Phagwara, Punjab, India – 144411"
line3 = "NAAC A++ Grade  |  NIRF Ranked  |  Est. 2005"

d.text((W//2 - len(line1)*9, 30),   line1, fill=(255, 255, 255), font=big)
d.text((W//2 - len(line2)*5, 90),   line2, fill=(200, 210, 255), font=small)
d.text((W//2 - len(line3)*4, 118),  line3, fill=(180, 200, 240), font=tiny)

img.save(OUT, "PNG")
print(f"Saved LPU logo -> {OUT}")
