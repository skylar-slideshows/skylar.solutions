#!/usr/bin/env python3
"""
add-thermistors.py
------------------
KiCad's 3D export puts an axial resistor model on every TH (thermistor)
footprint. This swaps each one for an epoxy bead thermistor whose leads bend
over so the bead touches the VCA it temperature-compensates:

  * AS2180 (on its vertical SIP adapter): against the adapter, right behind the chip
  * THAT2180A (SIP-8): against the side of the package
  * SSI2164 (on a castellated breakout under the card): flat on the chip

Nothing else in the model changes.

Usage (after converting with --parts):
  node tools/step-to-mesh.mjs CARD.step assets/models/card.bin --parts card.parts.json
  python3 tools/add-thermistors.py CARD.kicad_pcb assets/models/card.bin card.parts.json
"""
import json, math, re, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pcb3 import read_model, write_model, add_mesh, colour_index, remove_instances
from kicad_sexpr import parse, kids, kid, val, prop

VCA = re.compile(r"SSI2164|AS2180|THAT2180", re.I)
BOARD_TOP, BOARD_BOTTOM = 1.6, 0.0
LEAD_R = 0.25                    # 0.5 mm tinned leads
BEAD = (1.8, 1.3, 1.05)          # half-length (along the leads), half-width, half-thickness, mm
# bands from the lead end to the tip, as in a dipped bead thermistor
BANDS = [(0.0, 0.30, (222, 196, 92)), (0.30, 0.45, (168, 52, 50)), (0.45, 0.58, (102, 94, 168)),
         (0.58, 0.68, (168, 52, 50)), (0.68, 1.0, (112, 90, 62))]
LEAD_COLOUR = (204, 204, 208)

add = lambda a, b: [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
sub = lambda a, b: [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
mul = lambda a, k: [a[0] * k, a[1] * k, a[2] * k]
dot = lambda a, b: a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
cross = lambda a, b: [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
def norm(a):
    l = math.sqrt(dot(a, a)) or 1.0
    return [a[0] / l, a[1] / l, a[2] / l]
def clamp(x, lo, hi): return max(lo, min(hi, x))
def centre(p): return [(p["lo"][k] + p["hi"][k]) / 2 for k in range(3)]


def footprints(pcb_path):
    pcb = parse(open(pcb_path).read())
    out = []
    for fp in kids(pcb, "footprint"):
        at = kid(fp, "at"); x, y = float(at[1]), float(at[2]); a = float(at[3]) if len(at) > 3 else 0.0
        r = math.radians(a)
        pads = []
        for p in kids(fp, "pad"):
            pa = kid(p, "at"); px, py = float(pa[1]), float(pa[2])
            pads.append([x + px * math.cos(r) + py * math.sin(r), -(y - px * math.sin(r) + py * math.cos(r))])
        out.append({"ref": prop(fp, "Reference") or "", "val": prop(fp, "Value") or "", "x": x, "y": -y,
                    "side": "top" if val(fp, "layer") == "F.Cu" else "bottom", "pads": pads})
    return out


def nearest(parts, pattern, x, y, side=None):
    c = [p for p in parts if re.search(pattern, p["name"], re.I)]
    if side: c = [p for p in c if (centre(p)[2] > BOARD_TOP / 2) == (side == "top")]
    return min(c, key=lambda p: math.hypot(centre(p)[0] - x, centre(p)[1] - y))


def contact(th, chip, parts):
    """Where the bead touches: (point on the surface, outward normal, bead axis)."""
    tx = sum(p[0] for p in th["pads"]) / 2; ty = sum(p[1] for p in th["pads"]) / 2
    up = [0, 0, 1] if th["side"] == "top" else [0, 0, -1]
    if re.search("AS2180|THAT2180", chip["val"], re.I):
        if "AS2180" in chip["val"].upper():
            body = nearest(parts, r"as2180", chip["x"], chip["y"])               # the vertical adapter board
            ic = nearest(parts, r"SOIC-8", *centre(body)[:2])                   # the chip on it
        else:
            body = ic = nearest(parts, r"SIP-8", chip["x"], chip["y"])          # THAT2180A package
        k = 0 if body["hi"][0] - body["lo"][0] < body["hi"][1] - body["lo"][1] else 1   # thin axis
        o = 1 - k
        toward = 1 if (tx, ty)[k] > centre(body)[k] else -1
        point = [0, 0, 0]
        point[k] = body["hi"][k] if toward > 0 else body["lo"][k]
        if ic is body:   # SIP: meet it level with the thermistor, part way up
            point[o] = clamp((tx, ty)[o], body["lo"][o] + 2.5, body["hi"][o] - 2.5)
            point[2] = body["lo"][2] + 0.55 * (body["hi"][2] - body["lo"][2])
        else:            # adapter: right behind the chip
            point[o] = centre(ic)[o]; point[2] = centre(ic)[2]
        n = [0, 0, 0]; n[k] = toward
        return point, n, up
    # SSI2164 on a castellated breakout: lie flat on the chip's exposed face,
    # in this thermistor's slot along the chip (a quad VCA can have four)
    ic = th["ic"]
    face = ic["lo"][2] if th["side"] == "bottom" else ic["hi"][2]
    L = 1 if ic["hi"][1] - ic["lo"][1] > ic["hi"][0] - ic["lo"][0] else 0   # long axis
    A = 1 - L                                                              # across
    point = [0, 0, face]
    point[L] = ic["lo"][L] + (ic["hi"][L] - ic["lo"][L]) * (th["slot"] + 0.5) / th["slots"]
    point[A] = centre(ic)[A]
    axis = [0, 0, 0]; axis[A] = 1 if centre(ic)[A] > (tx, ty)[A] else -1
    return point, up, axis


def bead(center, axis, normal, colours, pos, nrm, col, tri, rings=6, seg=18, size=BEAD):
    a, b, c = size
    w = norm(cross(axis, normal))
    for t0, t1, rgb in BANDS:
        ci = colours[rgb]
        start = len(pos)
        for i in range(rings + 1):
            t = t0 + (t1 - t0) * i / rings
            s = -a + 2 * a * t
            r = math.sqrt(max(0.0, 1 - (s / a) ** 2))
            for j in range(seg):
                th = 2 * math.pi * j / seg
                cw, cn = b * r * math.cos(th), c * r * math.sin(th)
                pos.append(add(add(add(center, mul(axis, s)), mul(w, cw)), mul(normal, cn)))
                g = add(add(mul(axis, s / a ** 2), mul(w, cw / b ** 2)), mul(normal, cn / c ** 2))
                nrm.append(norm(g) if dot(g, g) > 1e-12 else axis)
                col.append(ci)
        for i in range(rings):
            for j in range(seg):
                p0 = start + i * seg + j; p1 = start + i * seg + (j + 1) % seg
                tri += [p0, p1, p0 + seg, p1, p1 + seg, p0 + seg]


def tube(path, colour, pos, nrm, col, tri, seg=10):
    start = len(pos)
    d0 = norm(sub(path[1], path[0]))
    u = norm(cross(d0, [0, 0, 1] if abs(d0[2]) < 0.9 else [1, 0, 0]))
    for i, p in enumerate(path):
        d = norm(sub(path[min(i + 1, len(path) - 1)], path[max(i - 1, 0)]))
        u = norm(sub(u, mul(d, dot(u, d))))   # parallel transport: no twisting round the bends
        v = cross(d, u)
        for j in range(seg):
            a = 2 * math.pi * j / seg
            o = add(mul(u, math.cos(a)), mul(v, math.sin(a)))
            pos.append(add(p, mul(o, LEAD_R))); nrm.append(o); col.append(colour)
    for i in range(len(path) - 1):
        for j in range(seg):
            p0 = start + i * seg + j; p1 = start + i * seg + (j + 1) % seg
            tri += [p0, p1, p0 + seg, p1, p1 + seg, p0 + seg]


def bezier(p0, p1, p2, p3, n=18):
    pts = []
    for i in range(n + 1):
        t = i / n; s = 1 - t
        pts.append([s**3 * p0[k] + 3 * s * s * t * p1[k] + 3 * s * t * t * p2[k] + t**3 * p3[k] for k in range(3)])
    return pts


def main(pcb_path, bin_path, parts_path):
    fps = footprints(pcb_path)
    parts = json.load(open(parts_path))
    model = read_model(bin_path)
    colours = {rgb: colour_index(model, rgb) for *_, rgb in BANDS}
    lead_ci = colour_index(model, LEAD_COLOUR)
    pos, nrm, col, tri, remove = [], [], [], [], []
    ths = [f for f in fps if re.match(r"TH\d", f["ref"])]
    for th in ths:
        chips = [f for f in fps if VCA.search(f["val"]) and f["side"] == th["side"]]
        th["chip"] = min(chips, key=lambda f: math.hypot(f["x"] - th["x"], f["y"] - th["y"]))
        if "SSI2164" in th["chip"]["val"].upper():
            th["ic"] = nearest(parts, r"SOIC-16", th["chip"]["x"], th["chip"]["y"], th["side"])
    groups = {}
    for th in ths:
        if "ic" in th: groups.setdefault(id(th["ic"]), []).append(th)
    for group in groups.values():
        ic = group[0]["ic"]
        L = 1 if ic["hi"][1] - ic["lo"][1] > ic["hi"][0] - ic["lo"][0] else 0
        for i, th in enumerate(sorted(group, key=lambda t: sum(p[L] for p in t["pads"]))):
            th["slot"], th["slots"] = i, len(group)
    for th in ths:
        # the resistor model KiCad placed on this footprint
        for i, p in enumerate(parts):
            if re.search(r"R_Axial", p["name"]) and math.hypot(centre(p)[0] - th["x"], centre(p)[1] - th["y"]) < 2.5:
                remove.append(i)
        chip = th["chip"]
        point, n, axis = contact(th, chip, parts)
        a, b, c = BEAD
        if "slots" in th:   # slim the beads a little if a chip is crowded
            span = th["ic"]["hi"][1] - th["ic"]["lo"][1] if th["ic"]["hi"][1] - th["ic"]["lo"][1] > th["ic"]["hi"][0] - th["ic"]["lo"][0] else th["ic"]["hi"][0] - th["ic"]["lo"][0]
            b = min(b, span / th["slots"] / 2 - 0.06)
        centre_ = add(point, mul(n, c + 0.05))
        base = add(centre_, mul(axis, -a * 0.92))
        bead(centre_, axis, n, colours, pos, nrm, col, tri, size=(a, b, c))
        # two leads: straight out through the board, then a smooth bend into the bead
        out = 1 if th["side"] == "top" else -1
        surface = BOARD_TOP if out > 0 else BOARD_BOTTOM
        w = norm(cross(axis, n))
        ends = sorted([add(base, mul(w, 0.55)), add(base, mul(w, -0.55))], key=lambda e: dot(e, w))
        pads = sorted(th["pads"], key=lambda p: dot([p[0], p[1], 0], w))
        for (px, py), end in zip(pads, ends):
            under = [px, py, (BOARD_BOTTOM - 1.0) if out > 0 else (BOARD_TOP + 1.0)]
            exitp = [px, py, surface + out * 1.2]
            L = math.dist(exitp, end)
            curve = bezier(exitp, add(exitp, [0, 0, out * min(L * 0.3, 3.0)]), add(end, mul(axis, -min(L * 0.35, 4.0))), end, n=24)
            tube([under] + curve, lead_ci, pos, nrm, col, tri)
        print(f"  {th['ref']:6s} ({th['side']}) -> {chip['ref']} {chip['val']}: bead at "
              f"({centre_[0]:.1f}, {centre_[1]:.1f}, {centre_[2]:.1f}), leads {L:.1f} mm")
    remove_instances(model, remove)
    add_mesh(model, pos, nrm, col, tri)
    write_model(model, bin_path)
    print(f"replaced {len(set(remove))} resistor parts with {len(ths)} thermistors; {len(model.palette)} colours")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
