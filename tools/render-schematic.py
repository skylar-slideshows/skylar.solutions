#!/usr/bin/env python3
"""Render a KiCad schematic sheet (.kicad_sch, KiCad 6 or later) as a clean SVG.

    python3 tools/render-schematic.py SHEET.kicad_sch OUT.svg [--check] [--bipolar C3,C4]

Everything on the sheet is drawn: every symbol from the sheet's embedded symbol
library (all units, pins, pin names and numbers, references and values, DNP marks),
wires, buses, junctions, no-connects, local, global and hierarchical labels, power
flags, notes, graphic boxes and hierarchical sheet blocks. Fields are placed the way
KiCad places them: their text box is turned with the symbol, then drawn centred, so
they never come out upside down.

The drawing is cropped to what's on the sheet (no page frame), in the site's colours on
a light paper background, so it reads the same in light and dark mode.

--check prints how many pin ends land on a wire, junction, label or another pin, which
is a quick test that the symbol transforms are right.

--bipolar lists electrolytics drawn with a polarized symbol that are really bipolar: they
are drawn without the "+" and with both plates alike.
"""
import html, math, re, sys
from kicad_sexpr import parse, kids, kid, val

FONT = "'Recursive', 'Inter', 'Helvetica Neue', Helvetica, Arial, sans-serif"
STYLE = """
.paper{fill:#fcfbff}
.w{stroke:#3d35a8;stroke-width:.2;fill:none;stroke-linecap:round;stroke-linejoin:round}
.bus{stroke:#3d35a8;stroke-width:.6;fill:none;stroke-linecap:round}
.s{stroke:#2a2840;stroke-width:.2;fill:none;stroke-linecap:round;stroke-linejoin:round}
.sf{fill:#f1edfd}
.so{fill:#2a2840}
.p{stroke:#2a2840;stroke-width:.17;fill:none;stroke-linecap:round}
.j{fill:#3d35a8}
.nc{stroke:#c2410c;stroke-width:.2;stroke-linecap:round}
.grp{stroke:#b6abeb;stroke-width:.22;fill:none;stroke-dasharray:1.1 .7}
.sheet{stroke:#6f5ae0;stroke-width:.28;fill:#f6f3ff}
.gl{stroke:#6f5ae0;stroke-width:.18;fill:#ede8fd;stroke-linejoin:round}
.hl{stroke:#6f5ae0;stroke-width:.18;fill:#ede8fd;stroke-linejoin:round}
.dnp{stroke:#e0526a;stroke-width:.3;stroke-linecap:round;opacity:.8}
text{font-family:%s;fill:#2a2840}
.ref{fill:#5b3fd6;font-weight:650}
.val{fill:#5f5878}
.fld{fill:#5f5878}
.pn{fill:#2a2840}
.pnum{fill:#8f88ad}
.lbl{fill:#3d2f8f;font-weight:600}
.note{fill:#6b6488;font-style:italic}
.shn{fill:#3d2f8f;font-weight:700}
""" % FONT

DEFAULT_TEXT = 1.27
HIDDEN_FIELDS = {"Footprint", "Datasheet", "Description", "Sim.Library", "Sim.Name", "Sim.Device", "Sim.Pins", "Sim.Params"}


def num(x): return float(x)


def flag(node, name):
    """KiCad 6 writes `hide` as a bare token, newer versions as (hide yes)."""
    if node is None: return False
    for k in node[1:]:
        if k == name: return True
        if isinstance(k, list) and k and k[0] == name: return len(k) == 1 or k[1] in ("yes", "true")
    return False


def effects_of(node):
    e = kid(node, "effects")
    size, bold, italic, hj, vj, hide = DEFAULT_TEXT, False, False, "center", "center", flag(node, "hide")
    if e is not None:
        f = kid(e, "font")
        if f is not None:
            sz = kid(f, "size")
            if sz: size = num(sz[1])
            bold, italic = flag(f, "bold"), flag(f, "italic")
        j = kid(e, "justify")
        if j:
            for t in j[1:]:
                if t in ("left", "right"): hj = t
                if t in ("top", "bottom"): vj = t
        hide = hide or flag(e, "hide")
    return dict(size=size, bold=bold, italic=italic, hj=hj, vj=vj, hide=hide)


def text_width(s, size, bold=False):
    return max(1, len(s)) * size * (0.66 if not bold else 0.7)


class SVG:
    def __init__(self):
        self.out, self.lo, self.hi = [], [math.inf, math.inf], [-math.inf, -math.inf]

    def grow(self, x, y, pad=0.0):
        self.lo[0] = min(self.lo[0], x - pad); self.lo[1] = min(self.lo[1], y - pad)
        self.hi[0] = max(self.hi[0], x + pad); self.hi[1] = max(self.hi[1], y + pad)

    def path(self, pts, cls, close=False):
        if len(pts) < 2: return
        for x, y in pts: self.grow(x, y)
        d = "M" + " L".join(f"{x:.3f} {y:.3f}" for x, y in pts) + (" Z" if close else "")
        self.out.append(f'<path class="{cls}" d="{d}"/>')

    def circle(self, x, y, r, cls):
        self.grow(x, y, r)
        self.out.append(f'<circle class="{cls}" cx="{x:.3f}" cy="{y:.3f}" r="{r:.3f}"/>')

    def text(self, s, x, y, size, angle=0, anchor="middle", valign="center", cls="", bold=False, italic=False):
        """valign: where (x, y) sits on the text: top, center, bottom (baseline-ish)."""
        if not s: return
        lines = s.replace("\\n", "\n").split("\n")        # KiCad stores line breaks as a \n escape
        lh = size * 1.45
        n = len(lines)
        if valign == "top": first = size * 0.78
        elif valign == "bottom": first = -lh * (n - 1)
        else: first = size * 0.36 - lh * (n - 1) / 2
        w = max(text_width(l, size, bold) for l in lines)
        # bounds (approximate, for cropping)
        dx0 = {"start": 0, "middle": -w / 2, "end": -w}[anchor]
        corners = [(dx0, first - size), (dx0 + w, first - size), (dx0, first + lh * (n - 1) + size * 0.25),
                   (dx0 + w, first + lh * (n - 1) + size * 0.25)]
        c, sn = math.cos(math.radians(-angle)), math.sin(math.radians(-angle))
        for u, v in corners: self.grow(x + u * c - v * sn, y + u * sn + v * c)
        attrs = f' class="{cls}"' if cls else ""
        style = (' font-weight="700"' if bold and "ref" not in cls and "shn" not in cls else "") + (' font-style="italic"' if italic else "")
        tspans = "".join(f'<tspan x="0" y="{first + i * lh:.3f}">{html.escape(l)}</tspan>' for i, l in enumerate(lines))
        rot = f" rotate({-angle:g})" if angle else ""
        self.out.append(f'<text{attrs}{style} font-size="{size:.3f}" text-anchor="{anchor}" transform="translate({x:.3f} {y:.3f}){rot}">{tspans}</text>')

    def svg(self, margin=6.0, scale=4.0):
        x0, y0 = self.lo[0] - margin, self.lo[1] - margin
        w, h = self.hi[0] - self.lo[0] + 2 * margin, self.hi[1] - self.lo[1] + 2 * margin
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.2f} {y0:.2f} {w:.2f} {h:.2f}" '
                f'width="{w * scale:.0f}" height="{h * scale:.0f}"><style>{STYLE}</style>'
                f'<rect class="paper" x="{x0:.2f}" y="{y0:.2f}" width="{w:.2f}" height="{h:.2f}"/>'
                + "".join(self.out) + "</svg>")


# --------------------------------------------------------------------------- transforms
def orientation(angle, mirror):
    """2x2 matrix taking library coordinates (y up) to sheet offsets (y down)."""
    a = int(round(angle)) % 360
    rot = {0: (1, 0, 0, 1), 90: (0, 1, -1, 0), 180: (-1, 0, 0, -1), 270: (0, -1, 1, 0)}[a]   # visual CCW, y down
    M = (rot[0], -rot[1], rot[2], -rot[3])                                                    # rot . diag(1, -1)
    if mirror == "x": M = (M[0], M[1], -M[2], -M[3])                                          # flip vertically
    if mirror == "y": M = (-M[0], -M[1], M[2], M[3])                                          # flip horizontally
    return M


def apply(M, origin, x, y):
    return origin[0] + M[0] * x + M[1] * y, origin[1] + M[2] * x + M[3] * y


# --------------------------------------------------------------------------- symbol library
class LibSymbol:
    def __init__(self, node):
        self.name = node[1]
        self.power = flag(node, "power") or kid(node, "power") is not None
        pn = kid(node, "pin_names")
        self.pin_names_hidden = flag(pn, "hide") if pn is not None else False
        off = kid(pn, "offset") if pn is not None else None
        self.pin_name_offset = num(off[1]) if off else 0.508
        pnum = kid(node, "pin_numbers")
        self.pin_numbers_hidden = flag(pnum, "hide") if pnum is not None else False
        self.units = {}            # (unit, style) -> list of graphic nodes
        for sub in kids(node, "symbol"):
            m = re.match(r".*_(\d+)_(\d+)$", sub[1])
            if not m: continue
            key = (int(m.group(1)), int(m.group(2)))
            self.units.setdefault(key, []).extend(k for k in sub[1:] if isinstance(k, list))
        self.unit_count = max([u for u, s in self.units] + [1])

    def items(self, unit, style):
        out = []
        for (u, s), items in self.units.items():
            if u in (0, unit) and s in (0, style): out += items
        return out


def stroke_class(node, base):
    return base


def draw_symbol_graphic(svg, g, M, origin):
    kind = g[0]
    fill = kid(g, "fill")
    ftype = val(fill, "type") if fill is not None else "none"
    fcls = {"background": "s sf", "outline": "s so", "color": "s sf"}.get(ftype, "s")
    if kind == "rectangle":
        (x0, y0), (x1, y1) = [(num(kid(g, k)[1]), num(kid(g, k)[2])) for k in ("start", "end")]
        pts = [apply(M, origin, x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        svg.path(pts, fcls, close=True)
    elif kind == "polyline":
        pts = [apply(M, origin, num(p[1]), num(p[2])) for p in kids(kid(g, "pts"), "xy")]
        closed = ftype in ("background", "outline", "color") and len(pts) > 2
        svg.path(pts, fcls if closed else "s", close=closed)
    elif kind == "circle":
        c = kid(g, "center"); r = num(val(g, "radius"))
        x, y = apply(M, origin, num(c[1]), num(c[2]))
        svg.circle(x, y, r, fcls)
    elif kind == "arc":
        p = [apply(M, origin, num(kid(g, k)[1]), num(kid(g, k)[2])) for k in ("start", "mid", "end")]
        svg.path(arc_points(*p), fcls if ftype in ("background", "outline") else "s", close=ftype in ("background", "outline"))
    elif kind == "bezier":
        pts = [apply(M, origin, num(p[1]), num(p[2])) for p in kids(kid(g, "pts"), "xy")]
        svg.path(pts, "s")


def arc_points(a, b, c, n=18):
    """Points along the circle through a, b, c, from a to c passing b."""
    ax, ay = a; bx, by = b; cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9: return [a, c]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    t0, tb, t1 = (math.atan2(p[1] - uy, p[0] - ux) for p in (a, b, c))
    def ccw(f, t): return (t - f) % (2 * math.pi)
    span = ccw(t0, t1)
    if ccw(t0, tb) > span: span -= 2 * math.pi                      # go the other way round
    return [(ux + r * math.cos(t0 + span * k / n), uy + r * math.sin(t0 + span * k / n)) for k in range(n + 1)]


def draw_pin(svg, lib, pin, M, origin, ends):
    at = kid(pin, "at"); L = num(val(pin, "length") or 2.54)
    x, y, a = num(at[1]), num(at[2]), num(at[3]) if len(at) > 3 else 0.0
    dx, dy = math.cos(math.radians(a)), math.sin(math.radians(a))
    p0 = apply(M, origin, x, y); p1 = apply(M, origin, x + dx * L, y + dy * L)
    ends.append(p0)
    if flag(pin, "hide"): return
    style = pin[2] if len(pin) > 2 and isinstance(pin[2], str) else "line"
    ux, uy = (p1[0] - p0[0]) / (L or 1), (p1[1] - p0[1]) / (L or 1)    # towards the body, sheet coordinates
    if "inverted" in style:
        r = 0.42
        svg.path([p0, (p1[0] - ux * 2 * r, p1[1] - uy * 2 * r)], "p")
        svg.circle(p1[0] - ux * r, p1[1] - uy * r, r, "p")
    else:
        svg.path([p0, p1], "p")
    if "clock" in style:
        px, py = -uy, ux
        svg.path([(p1[0] + px * 0.5, p1[1] + py * 0.5), (p1[0] + ux * 0.6, p1[1] + uy * 0.6), (p1[0] - px * 0.5, p1[1] - py * 0.5)], "p")
    horizontal = abs(ux) > abs(uy)
    angle = 0 if horizontal else 90
    name_node, num_node = kid(pin, "name"), kid(pin, "number")
    name = name_node[1] if name_node else ""
    number = num_node[1] if num_node else ""
    ne, nue = effects_of(name_node) if name_node else None, effects_of(num_node) if num_node else None
    mid = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
    show_name = not lib.pin_names_hidden and name not in ("", "~") and not (ne and ne["hide"])
    show_num = not lib.pin_numbers_hidden and number != "" and not (nue and nue["hide"])
    nsize = ne["size"] if ne else DEFAULT_TEXT
    msize = nue["size"] if nue else DEFAULT_TEXT
    name = re.sub(r"~\{([^}]*)\}", r"\1", name)
    if lib.pin_name_offset > 0:
        if show_name:
            o = lib.pin_name_offset
            q = (p1[0] + ux * o, p1[1] + uy * o)
            if horizontal: anchor = "start" if ux > 0 else "end"
            else: anchor = "start" if uy < 0 else "end"
            svg.text(name, q[0], q[1], nsize, angle, anchor, "center", "pn")
        if show_num:
            off = 0.25 + msize * 0.05
            q = (mid[0], mid[1] - off) if horizontal else (mid[0] - off, mid[1])
            svg.text(number, q[0], q[1], msize, angle, "middle", "bottom", "pnum")
    else:
        off = 0.25
        if show_name:
            q = (mid[0], mid[1] - off) if horizontal else (mid[0] - off, mid[1])
            svg.text(name, q[0], q[1], nsize, angle, "middle", "bottom", "pn")
        if show_num:
            q = (mid[0], mid[1] + off) if horizontal else (mid[0] + off, mid[1])
            svg.text(number, q[0], q[1], msize, angle, "middle", "top", "pnum")


def draw_field(svg, prop, M, origin, text, cls):
    """KiCad turns a field's text box with its symbol, then draws it centred, upright."""
    e = effects_of(prop)
    at = kid(prop, "at")
    px, py, fa = num(at[1]), num(at[2]), (num(at[3]) if len(at) > 3 else 0.0)
    w, h = text_width(text, e["size"], e["bold"]), e["size"]
    # the text box around the anchor, in the field's own (unturned) frame
    x0 = {"left": 0.0, "center": -w / 2, "right": -w}[e["hj"]]
    y0 = {"top": 0.0, "center": -h / 2, "bottom": -h}[e["vj"]]
    cx, cy = x0 + w / 2, y0 + h / 2
    if int(round(fa)) % 180 == 90: cx, cy = cy, -cx                      # vertical field: box turns with the text
    # sheet-space orientation of the symbol: M . diag(1, -1)
    S = (M[0], -M[1], M[2], -M[3])
    dx, dy = S[0] * cx + S[1] * cy, S[2] * cx + S[3] * cy
    vertical = (int(round(fa)) % 180 == 90) != (M[1] != 0)
    svg.text(text, px + dx, py + dy, e["size"], 90 if vertical else 0, "middle", "center", cls, e["bold"], e["italic"])


# --------------------------------------------------------------------------- sheet items
def label_shape(shape, L, hh):
    """Outline in a frame where the label runs along +u from its anchor (0, 0)."""
    if shape == "input":
        return [(0, 0), (hh, -hh), (L + hh, -hh), (L + hh, hh), (hh, hh)], hh
    if shape == "output":
        return [(0, -hh), (L, -hh), (L + hh, 0), (L, hh), (0, hh)], 0.0
    if shape in ("bidirectional", "tri_state"):
        return [(0, 0), (hh, -hh), (L + hh, -hh), (L + 2 * hh, 0), (L + hh, hh), (hh, hh)], hh
    return [(0, -hh), (L, -hh), (L, hh), (0, hh)], 0.0


def draw_labels(svg, sch, anchors):
    for lb in kids(sch, "label"):
        at = kid(lb, "at"); x, y, a = num(at[1]), num(at[2]), num(at[3]) if len(at) > 3 else 0
        e = effects_of(lb); anchors.append((x, y))
        a = int(round(a)) % 360
        horizontal = a in (0, 180)
        off = 0.35
        if horizontal:
            svg.text(lb[1], x, y - off, e["size"], 0, "start" if a == 0 else "end", "bottom", "lbl")
        else:
            svg.text(lb[1], x - off, y, e["size"], 90, "start" if a == 90 else "end", "bottom", "lbl")
    for kind, cls in (("global_label", "gl"), ("hierarchical_label", "hl")):
        for lb in kids(sch, kind):
            at = kid(lb, "at"); x, y, a = num(at[1]), num(at[2]), num(at[3]) if len(at) > 3 else 0
            e = effects_of(lb); anchors.append((x, y))
            shape = val(lb, "shape") or "passive"
            a = int(round(a)) % 360
            d = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}[a]
            n = (-d[1], d[0])
            to_sheet = lambda u, v: (x + d[0] * u + n[0] * v, y + d[1] * u + n[1] * v)
            tw = text_width(lb[1], e["size"], e["bold"])
            if kind == "global_label":
                hh, m = e["size"] * 0.78, e["size"] * 0.45
                pts, lead = label_shape(shape, tw + 2 * m, hh)
                svg.path([to_sheet(u, v) for u, v in pts], cls, close=True)
                centre = lead + m + tw / 2
            else:
                hh = e["size"] * 0.5
                pts, lead = label_shape(shape, e["size"] * 0.6, hh)
                svg.path([to_sheet(u, v) for u, v in pts], cls, close=True)
                centre = e["size"] * 0.6 + lead + 0.6 + tw / 2
            cx, cy = to_sheet(centre, 0)
            svg.text(lb[1], cx, cy, e["size"], 0 if a in (0, 180) else 90, "middle", "center", "lbl")


def draw_sheet_items(svg, sch):
    for r in kids(sch, "rectangle"):
        (x0, y0), (x1, y1) = [(num(kid(r, k)[1]), num(kid(r, k)[2])) for k in ("start", "end")]
        svg.path([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "grp", close=True)
    for pl in kids(sch, "polyline"):
        svg.path([(num(p[1]), num(p[2])) for p in kids(kid(pl, "pts"), "xy")], "grp")
    for t in kids(sch, "text"):
        at = kid(t, "at"); x, y, a = num(at[1]), num(at[2]), num(at[3]) if len(at) > 3 else 0
        e = effects_of(t)
        anchor = {"left": "start", "center": "middle", "right": "end"}[e["hj"]]
        svg.text(t[1], x, y, e["size"], a, anchor, e["vj"], "note", e["bold"], False)
    for sh in kids(sch, "sheet"):
        at, sz = kid(sh, "at"), kid(sh, "size")
        x, y, w, h = num(at[1]), num(at[2]), num(sz[1]), num(sz[2])
        svg.path([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], "sheet", close=True)
        for prop in kids(sh, "property"):
            e = effects_of(prop)
            if e["hide"]: continue
            pa = kid(prop, "at")
            txt = prop[2] if prop[1] == "Sheetname" else ("File: " + prop[2] if prop[1] == "Sheetfile" else prop[2])
            anchor = {"left": "start", "center": "middle", "right": "end"}[e["hj"]]
            svg.text(txt, num(pa[1]), num(pa[2]), e["size"], num(pa[3]) if len(pa) > 3 else 0, anchor, e["vj"],
                     "shn" if prop[1] == "Sheetname" else "fld", e["bold"])
        for pin in kids(sh, "pin"):
            pa = kid(pin, "at"); px, py, a = num(pa[1]), num(pa[2]), int(round(num(pa[3]) if len(pa) > 3 else 0)) % 360
            e = effects_of(pin)
            inward = {0: (-1, 0), 180: (1, 0), 90: (0, 1), 270: (0, -1)}[a]
            hh = e["size"] * 0.5
            pts, _ = label_shape(pin[2] if len(pin) > 2 else "passive", e["size"] * 0.6, hh)
            n = (-inward[1], inward[0])
            svg.path([(px + inward[0] * u + n[0] * v, py + inward[1] * u + n[1] * v) for u, v in pts], "hl", close=True)
            gap = e["size"] * 0.6 + hh + 0.6
            tx, ty = px + inward[0] * gap, py + inward[1] * gap
            horizontal = a in (0, 180)
            anchor = "start" if (inward[0] > 0 or inward[1] < 0) else "end"
            svg.text(pin[1], tx, ty, e["size"], 0 if horizontal else 90, anchor, "center", "lbl")


def instance_reference(sym, fallback):
    inst = kid(sym, "instances")
    if inst is not None:
        for proj in kids(inst, "project"):
            for path in kids(proj, "path"):
                r = val(path, "reference")
                if r: return r
    return fallback


def render(path, check=False, bipolar=()):
    sch = parse(open(path).read())
    libs = {s[1]: LibSymbol(s) for s in kids(kid(sch, "lib_symbols"), "symbol")}
    svg = SVG()
    ends, anchors = [], []
    draw_sheet_items(svg, sch)

    wires = []
    for wcls, kind in (("w", "wire"), ("bus", "bus")):
        for w in kids(sch, kind):
            pts = [(num(p[1]), num(p[2])) for p in kids(kid(w, "pts"), "xy")]
            svg.path(pts, wcls); wires.append(pts)
    for be in kids(sch, "bus_entry"):
        at, sz = kid(be, "at"), kid(be, "size")
        x, y = num(at[1]), num(at[2])
        svg.path([(x, y), (x + num(sz[1]), y + num(sz[2]))], "w")
    for j in kids(sch, "junction"):
        at = kid(j, "at"); d = num(val(j, "diameter") or 0) or 0.9
        svg.circle(num(at[1]), num(at[2]), d / 2 * 1.1, "j"); anchors.append((num(at[1]), num(at[2])))
    for nc in kids(sch, "no_connect"):
        at = kid(nc, "at"); x, y = num(at[1]), num(at[2]); s = 0.6
        svg.path([(x - s, y - s), (x + s, y + s)], "nc"); svg.path([(x - s, y + s), (x + s, y - s)], "nc")
        anchors.append((x, y))
    draw_labels(svg, sch, anchors)

    for sym in kids(sch, "symbol"):
        lib = libs.get(val(sym, "lib_id"))
        if lib is None: continue
        at = kid(sym, "at")
        origin = (num(at[1]), num(at[2]))
        M = orientation(num(at[3]) if len(at) > 3 else 0, val(sym, "mirror"))
        unit = int(val(sym, "unit") or 1)
        style = int(val(sym, "body_style") or val(sym, "convert") or 1)
        items = lib.items(unit, style)
        ref = instance_reference(sym, next((p[2] for p in kids(sym, "property") if p[1] == "Reference"), ""))
        if ref in bipolar:
            # a bipolar electrolytic: no "+" strokes, and the filled (negative) plate drawn open
            def plus_stroke(g):
                pts = [(num(q[1]), num(q[2])) for q in kids(kid(g, "pts"), "xy")] if g[0] == "polyline" else []
                return len(pts) == 2 and math.dist(*pts) <= 1.1
            items = [g for g in items if not plus_stroke(g)]
            items = [[k if not (isinstance(k, list) and k and k[0] == "fill") else ["fill", ["type", "none"]] for k in g]
                     if g[0] == "rectangle" else g for g in items]
        lo_n = len(svg.out)
        for g in items:
            if g[0] == "pin": continue
            if g[0] == "text":
                e = effects_of(g)
                if e["hide"]: continue
                ta = kid(g, "at"); x, y = apply(M, origin, num(ta[1]), num(ta[2]))
                svg.text(g[1], x, y, e["size"], 0, "middle", "center", "fld")
                continue
            draw_symbol_graphic(svg, g, M, origin)
        for g in items:
            if g[0] == "pin": draw_pin(svg, lib, g, M, origin, ends)
        for prop in kids(sym, "property"):
            name, value = prop[1], prop[2] if len(prop) > 2 else ""
            e = effects_of(prop)
            if e["hide"] or name in HIDDEN_FIELDS or name.startswith("ki_") or not value: continue
            if name == "Reference":
                value = instance_reference(sym, value)
                if lib.unit_count > 1 and not lib.power: value += chr(ord("A") + unit - 1)
                cls = "ref"
            elif name == "Value": cls = "val"
            else: cls = "fld"
            draw_field(svg, prop, M, origin, value, cls)
        if flag(sym, "dnp"):
            # a red cross over the part, as KiCad marks do-not-populate
            xs = [float(v) for o in svg.out[lo_n:] for v in re.findall(r"M([\d.-]+) ", o)]
            ys = [float(v) for o in svg.out[lo_n:] for v in re.findall(r"M[\d.-]+ ([\d.-]+)", o)]
            if xs and ys:
                x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
                svg.path([(x0, y0), (x1, y1)], "dnp"); svg.path([(x0, y1), (x1, y0)], "dnp")

    if check:
        pts = set()
        for w in wires:
            for p in w: pts.add((round(p[0], 2), round(p[1], 2)))
        targets = pts | {(round(x, 2), round(y, 2)) for x, y in anchors}
        endset = [(round(x, 2), round(y, 2)) for x, y in ends]
        from collections import Counter
        multi = Counter(endset)
        hit = sum(1 for p in endset if p in targets or multi[p] > 1 or on_wire(p, wires))
        print(f"{path}: {hit} of {len(endset)} pin ends land on a wire, junction, label or pin")
    return svg.svg()


def on_wire(p, wires):
    x, y = p
    for w in wires:
        for (x0, y0), (x1, y1) in zip(w, w[1:]):
            if min(x0, x1) - 0.01 <= x <= max(x0, x1) + 0.01 and min(y0, y1) - 0.01 <= y <= max(y0, y1) + 0.01:
                if abs((x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)) < 0.01: return True
    return False


if __name__ == "__main__":
    argv = sys.argv[1:]
    bip = ()
    if "--bipolar" in argv:
        i = argv.index("--bipolar"); bip = set(argv[i + 1].split(",")); del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    out = render(args[0], check="--check" in argv, bipolar=bip)
    if len(args) > 1: open(args[1], "w").write(out)
