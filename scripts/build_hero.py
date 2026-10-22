"""
Prepare the hero photo (Passu Cones on the KKH) for the animated header.

    python scripts/build_hero.py path/to/photo.jpg

1. Inpaint the painted centre-line dashes, so the page can draw moving dashes instead.
2. Write app/static/hero.webp (full) and hero-blur.webp (tiny, for the blurred backdrop).
3. Print the road centre-line in image coordinates for the animation (ROAD_PATH in fx.js).
"""
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "static"

# Centre line of the road, measured on the 750x880 photo (near -> far, up to the van)
PATH = [(400, 885), (392, 876), (364, 852), (348.5, 837), (337.5, 828), (330, 821.5), (323, 815), (315, 807.5), (306, 798), (301, 791)]
HORIZON_Y, K = 760.0, 120.0      # y = HORIZON_Y + K / z, z = 1 at the bottom edge


def x_at(y):
    ys = [p[1] for p in PATH][::-1]; xs = [p[0] for p in PATH][::-1]
    return float(np.interp(y, ys, xs))


def main(src):
    img = cv2.imread(str(src))
    h, w = img.shape[:2]
    assert (w, h) == (750, 880), (w, h)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask = np.zeros((h, w), np.uint8)
    for y in range(803, h):            # stop below the white van so it stays sharp
        cx = x_at(y)
        half = 3 + (y - HORIZON_Y) * 0.075          # corridor widens toward the viewer
        x0, x1 = int(cx - half), int(cx + half) + 1
        row = gray[y, max(0, x0):min(w, x1)]
        local = np.median(gray[y, max(0, x0 - 25):min(w, x1 + 25)])
        bright = row > max(local + 35, 140)
        mask[y, max(0, x0):min(w, x1)][bright] = 255
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=2)
    clean = cv2.inpaint(img, mask, 6, cv2.INPAINT_TELEA)
    cv2.imwrite(str(OUT / "hero.webp"), clean, [cv2.IMWRITE_WEBP_QUALITY, 84])
    small = cv2.resize(clean, (60, 70), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(OUT / "hero-blur.webp"), small, [cv2.IMWRITE_WEBP_QUALITY, 70])
    cv2.imwrite(str(ROOT / "docs" / "hero_mask_debug.png"), mask)
    print("masked px:", int((mask > 0).sum()), "| hero.webp", (OUT / "hero.webp").stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main(sys.argv[1])
