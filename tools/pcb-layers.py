"""Render each copper layer of a .kicad_pcb as an SVG tile, plus an all-layers tile.

Usage: python3 tools/pcb-layers.py <board.kicad_pcb> <out_dir> <prefix>
Prints JSON describing each layer (what it carries) for captions.
"""
import json, math, sys, collections, os
sys.path.insert(0, os.path.dirname(__file__))
from kicad_sexpr import parse, kids, kid, val

COLORS = {
    "F.Cu": "#ff6b6b", "In1.Cu": "#f6c945", "In2.Cu": "#5fd39a", "In3.Cu": "#c48af0",
    "In4.Cu": "#ff9f45", "B.Cu": "#5aa7ff",
}
POWER = {"+15V", "-15V", "+48V", "+3V3", "-2V5", "+5V", "+12V", "-12V", "+3.3V", "VCC", "VDD"}
BG, BOARD, EDGE = "#14131c", "#1f1e2b", "#a9a6c4"
PX_PER_MM = 12


def f(v): return float(v)


def rot(px, py, a):
    r = math.radians(a)
    return px * math.cos(r) + py * math.sin(r), -px * math.sin(r) + py * math.cos(r)


def arc_points(s, m, e, n=24):
    (x1, y1), (x2, y2), (x3, y3) = s, m, e
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12: return [s, e]
    ux = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1) + (x3**2 + y3**2) * (y1 - y2)) / d
    uy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3) + (x3**2 + y3**2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a1, a2, a3 = (math.atan2(p[1] - uy, p[0] - ux) for p in (s, m, e))
    def norm(a, ref):
        while a < ref: a += 2 * math.pi
        return a
    a2n, a3n = norm(a2, a1), norm(a3, a1)
    if a2n > a3n: a3n -= 2 * math.pi  # go the other way round
    return [(ux + r * math.cos(a1 + (a3n - a1) * t / n), uy + r * math.sin(a1 + (a3n - a1) * t / n)) for t in range(n + 1)]


def board_outline(pcb):
    segs = []
    for g in pcb:
        if not (isinstance(g, list) and g and g[0] in ("gr_line", "gr_arc", "gr_rect", "gr_poly", "gr_circle")): continue
        if val(g, "layer") != "Edge.Cuts": continue
        if g[0] == "gr_line":
            segs.append([tuple(map(f, kid(g, "start")[1:3])), tuple(map(f, kid(g, "end")[1:3]))])
        elif g[0] == "gr_arc":
            segs.append(arc_points(tuple(map(f, kid(g, "start")[1:3])), tuple(map(f, kid(g, "mid")[1:3])), tuple(map(f, kid(g, "end")[1:3]))))
        elif g[0] == "gr_rect":
            (x1, y1), (x2, y2) = tuple(map(f, kid(g, "start")[1:3])), tuple(map(f, kid(g, "end")[1:3]))
            return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        elif g[0] == "gr_poly":
            return [tuple(map(f, p[1:3])) for p in kids(kid(g, "pts"), "xy")]
    if not segs: return None
    # chain the pieces into one loop
    loop = list(segs.pop(0))
    close = lambda a, b: abs(a[0] - b[0]) < 1e-3 and abs(a[1] - b[1]) < 1e-3
    while segs:
        for i, s in enumerate(segs):
            if close(s[0], loop[-1]): loop += s[1:]; segs.pop(i); break
            if close(s[-1], loop[-1]): loop += s[::-1][1:]; segs.pop(i); break
        else:
            break
    return loop


def poly_area(pts):
    return abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts)))) / 2


def render(path, out_dir, prefix):
    os.makedirs(out_dir, exist_ok=True)
    pcb = parse(open(path).read())
    copper = [l[1] for l in kid(pcb, "layers")[1:] if isinstance(l, list) and l[1].endswith(".Cu")]
    order = ["F.Cu"] + sorted([c for c in copper if c.startswith("In")], key=lambda s: int(s[2:-3])) + ["B.Cu"]
    outline = board_outline(pcb)
    xs = [p[0] for p in outline]; ys = [p[1] for p in outline]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    pad = 1.5
    W, H = (maxx - minx + 2 * pad), (maxy - miny + 2 * pad)
    board_area = poly_area(outline)

    tracks = collections.defaultdict(list)       # layer -> (x1,y1,x2,y2,w,net)
    for s in kids(pcb, "segment"):
        a, b = kid(s, "start"), kid(s, "end")
        tracks[val(s, "layer")].append((f(a[1]), f(a[2]), f(b[1]), f(b[2]), f(val(s, "width")), val(s, "net") or ""))
    vias = [(f(kid(v, "at")[1]), f(kid(v, "at")[2]), f(val(v, "size")), f(val(v, "drill"))) for v in kids(pcb, "via")]
    zones = collections.defaultdict(list)         # layer -> (net, pts)
    for z in kids(pcb, "zone"):
        net = val(z, "net") or ""
        for fp in kids(z, "filled_polygon"):
            pts = [(f(p[1]), f(p[2])) for p in kids(kid(fp, "pts"), "xy")]
            if len(pts) > 2: zones[val(fp, "layer")].append((net, pts))
    pads = collections.defaultdict(list)          # layer -> shapes
    holes = []
    for fpn in kids(pcb, "footprint"):
        at = kid(fpn, "at"); fx, fy = f(at[1]), f(at[2]); fa = f(at[3]) if len(at) > 3 else 0.0
        for p in kids(fpn, "pad"):
            pat = kid(p, "at"); px, py = f(pat[1]), f(pat[2]); pa = f(pat[3]) if len(pat) > 3 else 0.0
            dx, dy = rot(px, py, fa)
            cx, cy = fx + dx, fy + dy
            size = kid(p, "size"); w, h = f(size[1]), f(size[2])
            shape = p[3] if len(p) > 3 else "rect"
            layers = (kid(p, "layers") or ["layers"])[1:]
            cu = order if any(l in ("*.Cu", "F&B.Cu") for l in layers) else [l for l in layers if l in order]
            rr = 0.0
            if shape == "roundrect": rr = f(val(p, "roundrect_rratio", default=0.25)) * min(w, h)
            if shape == "oval": rr = min(w, h) / 2
            for l in cu: pads[l].append((shape, cx, cy, w, h, pa, rr))
            d = kid(p, "drill")
            if d and len(d) > 1:
                if d[1] == "oval": holes.append((cx, cy, f(d[2]) / 2))
                else: holes.append((cx, cy, f(d[1]) / 2))

    T = lambda x, y: ((x - minx + pad) * PX_PER_MM, (y - miny + pad) * PX_PER_MM)
    pts_attr = lambda pts: " ".join("%.1f,%.1f" % T(*p) for p in pts)

    def pad_svg(s, color):
        shape, cx, cy, w, h, pa, rr = s
        X, Y = T(cx, cy)
        if shape == "circle":
            return f'<circle cx="{X:.1f}" cy="{Y:.1f}" r="{w / 2 * PX_PER_MM:.1f}" fill="{color}"/>'
        return (f'<rect x="{X - w / 2 * PX_PER_MM:.1f}" y="{Y - h / 2 * PX_PER_MM:.1f}" width="{w * PX_PER_MM:.1f}" height="{h * PX_PER_MM:.1f}" '
                f'rx="{rr * PX_PER_MM:.1f}" fill="{color}" transform="rotate({-pa:.1f} {X:.1f} {Y:.1f})"/>')

    def layer_body(layer, color, zone_op=0.42, track_op=1.0):
        # (zone_op: planes are drawn solid; pours on signal layers stay faint)
        out = []
        for net, pts in zones.get(layer, []):
            out.append(f'<polygon points="{pts_attr(pts)}" fill="{color}" fill-opacity="{zone_op}"/>')
        for x1, y1, x2, y2, w, net in tracks.get(layer, []):
            (X1, Y1), (X2, Y2) = T(x1, y1), T(x2, y2)
            out.append(f'<line x1="{X1:.1f}" y1="{Y1:.1f}" x2="{X2:.1f}" y2="{Y2:.1f}" stroke-width="{w * PX_PER_MM:.1f}"/>')
        g = f'<g stroke="{color}" stroke-linecap="round" stroke-opacity="{track_op}">' + "".join(o for o in out if o.startswith("<line")) + "</g>"
        zs = "".join(o for o in out if o.startswith("<polygon"))
        ps = "".join(pad_svg(s, color) for s in pads.get(layer, []))
        vs = "".join(f'<circle cx="{T(x, y)[0]:.1f}" cy="{T(x, y)[1]:.1f}" r="{sz / 2 * PX_PER_MM:.1f}" fill="{color}"/>' for x, y, sz, d in vias)
        return zs + g + ps + vs

    def frame(body, label=None):
        Wp, Hp = W * PX_PER_MM, H * PX_PER_MM
        hs = "".join(f'<circle cx="{T(x, y)[0]:.1f}" cy="{T(x, y)[1]:.1f}" r="{r * PX_PER_MM:.1f}" fill="{BG}"/>' for x, y, r in holes)
        hs += "".join(f'<circle cx="{T(x, y)[0]:.1f}" cy="{T(x, y)[1]:.1f}" r="{d / 2 * PX_PER_MM:.1f}" fill="{BG}"/>' for x, y, sz, d in vias)
        lab = ""
        if label:
            lx, ly, text = label
            fs = max(28, min(54, W * PX_PER_MM / 7))
            lab = (f'<text x="{lx:.1f}" y="{ly:.1f}" font-family="Recursive, ui-sans-serif, system-ui, sans-serif" font-size="{fs:.0f}" font-weight="800" '
                   f'text-anchor="middle" fill="#ffffff" stroke="{BG}" stroke-width="{fs / 6:.1f}" paint-order="stroke" letter-spacing="1">{text}</text>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {Wp:.0f} {Hp:.0f}" width="{Wp:.0f}" height="{Hp:.0f}">'
                f'<rect width="100%" height="100%" fill="{BG}"/>'
                f'<polygon points="{pts_attr(outline)}" fill="{BOARD}"/>' + body + hs +
                f'<polygon points="{pts_attr(outline)}" fill="none" stroke="{EDGE}" stroke-width="2"/>' + lab + "</svg>")

    info = []
    for layer in order:
        color = COLORS.get(layer, "#dddddd")
        zarea = collections.Counter()
        for net, pts in zones.get(layer, []): zarea[net] += poly_area(pts)
        tl = collections.Counter()
        for x1, y1, x2, y2, w, net in tracks.get(layer, []):
            cat = "GND" if net == "GND" else "power" if net in POWER else "signal"
            tl[cat] += math.hypot(x2 - x1, y2 - y1)
        power_nets = sorted({net for *_, net in tracks.get(layer, []) if net in POWER})
        # A ground/power plane is an inner layer that's nearly all copper with no
        # traces of its own. Everything else is a signal layer, even with a pour.
        routed = tl.get("signal", 0) + tl.get("power", 0)
        is_plane = layer.startswith("In") and routed < 5 and zarea and max(zarea.values()) / board_area > 0.6
        label = None
        if is_plane:
            net, _ = zarea.most_common(1)[0]
            biggest = max((pts for n, pts in zones[layer] if n == net), key=poly_area)
            cx = sum(p[0] for p in biggest) / len(biggest); cy = sum(p[1] for p in biggest) / len(biggest)
            X, Y = T(cx, cy)
            label = (X, Y, net)
        svg = frame(layer_body(layer, color, zone_op=0.42 if is_plane else 0.2), label)
        name = f"{prefix}-{layer.replace('.', '').lower()}"
        open(os.path.join(out_dir, name + ".svg"), "w").write(svg)
        info.append({"layer": layer, "file": name, "color": color, "role": "plane" if is_plane else "signal",
                     "plane_net": label[2] if label else None,
                     "pour": {k: round(v / board_area, 3) for k, v in zarea.items()},
                     "tracks_mm": {k: round(v) for k, v in tl.items()}, "power_nets": power_nets})
    # all layers, bottom to top
    body = "".join(layer_body(l, COLORS.get(l, "#ddd"), zone_op=0.16, track_op=0.9) for l in reversed(order))
    open(os.path.join(out_dir, f"{prefix}-all.svg"), "w").write(frame(body))
    info.append({"layer": "all", "file": f"{prefix}-all", "size_mm": [round(maxx - minx, 1), round(maxy - miny, 1)]})
    print(json.dumps(info))


if __name__ == "__main__":
    render(sys.argv[1], sys.argv[2], sys.argv[3])
