#!/usr/bin/env python3
"""
add-chip-marking.py
-------------------
Laser-etch style text on top of a chip in a converted model, e.g. the part
number on the STM32. The text becomes flat geometry just above the package top,
lined up with the package's own axes (pin 1 at the top left).

Usage (after converting with --parts):
  python3 tools/add-chip-marking.py MODEL.bin PARTS.json "LQFP-100" "STM32G474" "VET3"
"""
import json, os, re, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw, ImageFont
from pcb3 import read_model, write_model, add_mesh, colour_index

MARK = (150, 150, 155)       # laser marking on black epoxy reads as light grey
CAP_MM = 1.25                # letter height on the package
LINE_GAP = 0.55              # space between lines, as a fraction of letter height
OVERLAY = 1 | (3 << 1)       # draw like silkscreen: pulled toward the viewer

def font():
    for pat in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/**/DejaVuSans.ttf",
                "/usr/share/fonts/**/LiberationSans-Regular.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        hits = glob.glob(pat, recursive=True)
        if hits: return ImageFont.truetype(hits[0], 160)
    return ImageFont.load_default()

def text_mask(lines):
    f = font()
    cap = f.getbbox("H")[3] - f.getbbox("H")[1]
    widths = [f.getbbox(t)[2] for t in lines]
    W, gap = max(widths) + 20, int(cap * LINE_GAP)
    H = len(lines) * cap + (len(lines) - 1) * gap + 20
    im = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(im)
    y = 10
    for t, w in zip(lines, widths):
        d.text(((W - w) / 2, y - f.getbbox("H")[1]), t, font=f, fill=255)
        y += cap + gap
    return im.point(lambda v: 255 if v >= 128 else 0), CAP_MM / cap

def rectangles(mask):
    """Runs of lit pixels, merged downwards into rectangles."""
    W, H = mask.size
    px = mask.load()
    open_runs, rects = {}, []
    for y in range(H + 1):
        runs = set()
        if y < H:
            x = 0
            while x < W:
                if px[x, y]:
                    x0 = x
                    while x < W and px[x, y]: x += 1
                    runs.add((x0, x))
                else:
                    x += 1
        for r in list(open_runs):
            if r not in runs: rects.append((r[0], open_runs.pop(r), r[1], y))
        for r in runs:
            open_runs.setdefault(r, y)
    return rects

def main(bin_path, parts_path, pattern, *lines):
    parts = json.load(open(parts_path))
    chip = max((p for p in parts if re.search(pattern, p["name"])), key=lambda p: p["hi"][2] - p["lo"][2])
    m = chip["m"]
    u = [m[0], m[4], m[8]]; v = [m[1], m[5], m[9]]     # the package's own x and y axes
    c = [(chip["lo"][0] + chip["hi"][0]) / 2, (chip["lo"][1] + chip["hi"][1]) / 2, chip["hi"][2] + 0.02]
    mask, s = text_mask(lines)
    W, H = mask.size
    model = read_model(bin_path)
    ci = colour_index(model, MARK)
    pos, nrm, col, tri = [], [], [], []
    for x0, y0, x1, y1 in rectangles(mask):
        base = len(pos)
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            X, Y = (x - W / 2) * s, -(y - H / 2) * s
            pos.append([c[k] + u[k] * X + v[k] * Y for k in range(3)])
            nrm.append([0, 0, 1]); col.append(ci)
        tri += [base, base + 2, base + 1, base, base + 3, base + 2]
    add_mesh(model, pos, nrm, col, tri, flags=OVERLAY)
    write_model(model, bin_path)
    print(f"marked {chip['name']} with {' / '.join(lines)}: {len(tri) // 3} triangles, {W * s:.1f} x {H * s:.1f} mm")

if __name__ == "__main__":
    if len(sys.argv) < 5: sys.exit(__doc__)
    main(*sys.argv[1:])
