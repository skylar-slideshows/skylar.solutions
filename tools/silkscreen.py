"""The faceplate silkscreen (tools/faceplate-silkscreen.svg): parsing, and flat 3D geometry.

The art is one channel strip in points, y down from the top-left corner (36.3 x 500 mm is
102.9 x 1417.3 pt). parse() returns its labels and boxes in millimetres, y still down.
mesh() turns them into flat geometry just above the panel: glyph outlines from DejaVu Sans
Condensed Bold, each string stretched to the width Arial Bold (the art's font) gives it, so
labels still sit in their boxes.
"""
import math, re
from geom import Mesh, earcut, rrect

PT = 25.4 / 72
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"
# the art's classes: text size (pt) and colour, or box fill / outline width (pt)
TEXT = {"st0": (3.97, "grey"), "st1": (3.83, "white"), "st2": (4.54, "white"), "st3": (3.97, "white"),
        "st4": (6.24, "white"), "st5": (6.24, "grey"), "st6": (7.37, "white")}
BOX = {"st8": ("fill", 0.0), "st9": ("outline", 0.71), "st10": ("outline", 0.57)}
# Arial Bold advance widths per 1000 em (metric-compatible with Helvetica Bold)
ARIAL_BOLD = dict(zip(" ABCDEFGHIJKLMNOPQRSTUVWXYZ", [278, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833,
                                                       722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611]))
ARIAL_BOLD.update(zip("abcdefghijklmnopqrstuvwxyz", [556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889,
                                                       611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500]))
ARIAL_BOLD.update({c: 556 for c in "0123456789"})
ARIAL_BOLD.update({".": 278, ":": 333, "/": 278, "-": 333, "+": 584, "\u2212": 584, "\u221e": 713})


def parse(path):
    svg = open(path, encoding="utf-8").read()
    texts, boxes = [], []
    for m in re.finditer(r'<text[^>]*class="(st\d+)"[^>]*transform="translate\(([-\d.]+) ([-\d.]+)\)"[^>]*>\s*<tspan[^>]*>([^<]*)</tspan>', svg):
        cls, x, y, s = m.group(1), float(m.group(2)), float(m.group(3)), m.group(4)
        size, colour = TEXT[cls]
        texts.append({"x": x * PT, "y": y * PT, "size": size * PT, "colour": colour, "text": s,
                      "width": sum(ARIAL_BOLD.get(c, 556) for c in s) / 1000 * size * PT})
    for m in re.finditer(r'<rect[^>]*class="(st\d+)"([^>]*)/>', svg):
        a = dict(re.findall(r'(\w+)="([-\d.]+)"', m.group(2)))
        kind, sw = BOX[m.group(1)]
        boxes.append({"x": float(a["x"]) * PT, "y": float(a["y"]) * PT, "w": float(a["width"]) * PT,
                      "h": float(a["height"]) * PT, "r": float(a.get("rx", 0)) * PT, "kind": kind, "stroke": sw * PT})
    return texts, boxes


def _flat(m, outer, holes, z, ci):
    """A flat, upward-facing polygon with holes at height z."""
    flat, idx, pts = [], [], []
    for k, loop in enumerate([outer] + holes):
        if k: idx.append(len(pts))
        pts += loop
        for x, y in loop: flat += [x, y]
    tris = earcut(flat, idx)
    base = [m.vert((x, y, z), (0, 0, 1), ci) for x, y in pts]
    for t in range(0, len(tris), 3):
        a, b, c = tris[t:t + 3]
        (ax, ay), (bx, by), (cx, cy) = pts[a], pts[b], pts[c]
        if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) < 0: b, c = c, b
        m.tri += [base[a], base[b], base[c]]


def _area(loop):
    return 0.5 * sum(loop[i - 1][0] * loop[i][1] - loop[i][0] * loop[i - 1][1] for i in range(len(loop)))


def _inside(p, loop):
    x, y, c = p[0], p[1], False
    for i in range(len(loop)):
        (x0, y0), (x1, y1) = loop[i - 1], loop[i]
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0: c = not c
    return c


def _glyph_loops(font, ch):
    """Flattened contours of one glyph, in font units (y up)."""
    from fontTools.pens.basePen import BasePen
    class Pen(BasePen):
        def __init__(self, gs):
            super().__init__(gs); self.loops, self.cur = [], None
        def _moveTo(self, p): self.cur = [p]
        def _lineTo(self, p): self.cur.append(p)
        def _qCurveToOne(self, p1, p2):
            p0 = self.cur[-1]
            for k in range(1, 6):
                t = k / 5
                self.cur.append(((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
                                 (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]))
        def _curveToOne(self, p1, p2, p3):
            p0 = self.cur[-1]
            for k in range(1, 7):
                t = k / 6; u = 1 - t
                self.cur.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                                 u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
        def _closePath(self):
            if self.cur and len(self.cur) > 2:
                if self.cur[0] == self.cur[-1]: self.cur.pop()
                self.loops.append(self.cur)
            self.cur = None
        _endPath = _closePath
    gs = font.getGlyphSet()
    name = font.getBestCmap().get(ord(ch))
    if name is None: return [], 0
    pen = Pen(gs); gs[name].draw(pen)
    return pen.loops, font["hmtx"][name][0]


def mesh(path, colours, z, height=500.0):
    """Silkscreen geometry for one strip in panel coordinates (x, y up, mm), top faces at z.
    colours: {"white": palette index, "grey": palette index}."""
    from fontTools.ttLib import TTFont
    font = TTFont(FONT)
    upem = font["head"].unitsPerEm
    texts, boxes = parse(path)
    m = Mesh()
    for b in boxes:
        cx, cy = b["x"] + b["w"] / 2, height - (b["y"] + b["h"] / 2)
        if b["kind"] == "fill":
            _flat(m, rrect(cx, cy, b["w"], b["h"], b["r"], 15), [], z, colours["white"])
        else:
            s = b["stroke"] / 2
            outer = rrect(cx, cy, b["w"] + 2 * s, b["h"] + 2 * s, b["r"] + s, 15)
            inner = rrect(cx, cy, b["w"] - 2 * s, b["h"] - 2 * s, max(b["r"] - s, 0.01), 15)[::-1]
            _flat(m, outer, [inner], z, colours["white"])
    cache = {}
    for t in texts:
        scale = t["size"] / upem
        natural = sum((_glyph_loops(font, c)[1] if c not in cache else cache[c][1]) for c in t["text"]) * scale
        stretch = t["width"] / natural if natural else 1.0
        pen_x = 0.0
        tz = z + (0.012 if t["colour"] == "grey" else 0.0)     # dark text sits on its white box
        for ch in t["text"]:
            if ch not in cache: cache[ch] = _glyph_loops(font, ch)
            loops, adv = cache[ch]
            placed = [[(t["x"] + (pen_x + gx * scale) * stretch, height - t["y"] + gy * scale) for gx, gy in loop] for loop in loops]
            outers = [l for l in placed if _area(l) < 0]           # TrueType: outer contours run clockwise
            holes = [l for l in placed if _area(l) >= 0]
            for o in outers:
                mine = [h for h in holes if _inside(h[0], o)]
                _flat(m, o[::-1], [h[::-1] for h in mine], tz, colours[t["colour"]])
            pen_x += adv * scale
    return m, len(texts), len(boxes)
