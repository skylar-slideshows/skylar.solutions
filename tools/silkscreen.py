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
# Arial Bold advance widths per 1000 em (metric-compatible with Helvetica Bold)
ARIAL_BOLD = dict(zip(" ABCDEFGHIJKLMNOPQRSTUVWXYZ", [278, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833,
                                                       722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611]))
ARIAL_BOLD.update(zip("abcdefghijklmnopqrstuvwxyz", [556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889,
                                                       611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500]))
ARIAL_BOLD.update({c: 556 for c in "0123456789"})
ARIAL_BOLD.update({".": 278, ":": 333, "/": 278, "-": 333, "+": 584, "\u2212": 584, "\u221e": 713})


def _styles(svg):
    """Each class's declarations, from the art's own <style> block."""
    css = re.search(r"<style>(.*?)</style>", svg, re.S).group(1)
    props = {}
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        decl = {k.strip(): v.strip() for k, v in (d.split(":", 1) for d in body.split(";") if ":" in d)}
        for cls in re.findall(r"\.(st\d+)", sel):
            props.setdefault(cls, {}).update(decl)
    return props


def _num(v):
    return float(re.sub(r"px$", "", v))


def _colour(v):
    return "grey" if v.lower() in ("#4a4a4a",) else "white"


def parse(path):
    """Labels, boxes and lines of the art, in millimetres (y down).

    Walks the SVG with styles inherited from parent groups (as CSS does), and leaves out
    the reference layer (any group whose id contains REFERENCE), anything hidden with
    display:none, and anything placed outside the artboard."""
    import xml.etree.ElementTree as ET
    raw = open(path, encoding="utf-8").read()
    st = _styles(raw)
    root = ET.fromstring(raw.encode("utf-8"))
    NS = "{http://www.w3.org/2000/svg}"
    W, H = [float(v) for v in root.get("viewBox").replace(",", " ").split()[2:4]]
    INHERITED = ("fill", "stroke", "stroke-width", "font-size", "font-weight", "font-family")
    texts, boxes, lines = [], [], []

    def props(el, parent):
        p = {k: v for k, v in parent.items() if k in INHERITED}
        for cls in (el.get("class") or "").split(): p.update(st.get(cls, {}))
        for k in INHERITED + ("display",):
            if el.get(k) is not None: p[k] = el.get(k)
        for d in (el.get("style") or "").split(";"):
            if ":" in d:
                k, v = d.split(":", 1); p[k.strip()] = v.strip()
        return p

    def shift(el):
        m = re.search(r"translate\(\s*([-\d.eE]+)[\s,]+([-\d.eE]+)\s*\)", el.get("transform") or "")
        return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)

    def on_board(x, y):
        return -0.5 <= x <= W + 0.5 and -0.5 <= y <= H + 0.5

    def walk(el, parent, ox, oy):
        if "REFERENCE" in (el.get("id") or "").upper(): return
        p = props(el, parent)
        if p.get("display") == "none": return
        dx, dy = shift(el); ox, oy = ox + dx, oy + dy
        tag = el.tag.replace(NS, "")
        fill, stroke = p.get("fill", "#000"), p.get("stroke", "none")
        sw = _num(p.get("stroke-width", "1")) if stroke != "none" else 0.0
        if tag == "text":
            spans = [t for t in el.iter(NS + "tspan")]
            text = "".join((t.text or "") for t in spans) if spans else (el.text or "")
            first = spans[0] if spans else el
            x = ox + float(el.get("x", 0) or 0) + float(first.get("x", 0) or 0)
            y = oy + float(el.get("y", 0) or 0) + float(first.get("y", 0) or 0)
            if text.strip() and on_board(x, y):
                size = _num(p.get("font-size", "12"))
                texts.append({"x": x * PT, "y": y * PT, "size": size * PT, "text": text,
                              "colour": None if fill == "none" else _colour(fill),     # None: outline only
                              "stroke": sw * PT, "width": sum(ARIAL_BOLD.get(c, 556) for c in text) / 1000 * size * PT})
            return
        if tag == "rect":
            x, y = ox + float(el.get("x", 0)), oy + float(el.get("y", 0))
            if on_board(x, y):
                filled = fill != "none" and stroke == "none"
                boxes.append({"x": x * PT, "y": y * PT, "w": float(el.get("width")) * PT, "h": float(el.get("height")) * PT,
                              "r": float(el.get("rx", 0) or 0) * PT, "kind": "fill" if filled else "outline",
                              "stroke": 0.0 if filled else sw * PT})
        elif tag == "line":
            x1, y1 = ox + float(el.get("x1", 0)), oy + float(el.get("y1", 0))
            x2, y2 = ox + float(el.get("x2", 0)), oy + float(el.get("y2", 0))
            if on_board(x1, y1) and on_board(x2, y2):
                lines.append({"x1": x1 * PT, "y1": y1 * PT, "x2": x2 * PT, "y2": y2 * PT, "stroke": sw * PT})
        for child in el: walk(child, p, ox, oy)

    walk(root, {}, 0.0, 0.0)
    return texts, boxes, lines


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
    texts, boxes, lines = parse(path)
    m = Mesh()
    for l in lines:
        x1, y1, x2, y2 = l["x1"], height - l["y1"], l["x2"], height - l["y2"]
        d = math.hypot(x2 - x1, y2 - y1) or 1.0
        nx, ny = -(y2 - y1) / d * l["stroke"] / 2, (x2 - x1) / d * l["stroke"] / 2
        _flat(m, [(x1 - nx, y1 - ny), (x2 - nx, y2 - ny), (x2 + nx, y2 + ny), (x1 + nx, y1 + ny)], [], z, colours["white"])
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
        if t["colour"] is None: continue          # an outline-only copy sits on its filled twin
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
    return m, len(texts), len(boxes) + len(lines)
