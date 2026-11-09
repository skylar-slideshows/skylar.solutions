#!/usr/bin/env python3
"""Fix-ups for parts the card STEP exports get wrong.

    python3 tools/fix-cards.py BOARD.kicad_pcb model.bin parts.json [options]

  --film             grey film-cap bodies on C_Rect footprints that came out with no model,
                     and red film-cap models turned grey
  --bipolar EXCEPT   the electrolytics are bipolar: remove the polarity band from the can
                     and the "+" from the silkscreen. EXCEPT lists references to leave
                     polarized (comma separated, or "-" for none)
  --soic REF         a SOIC body on footprint REF, for an op-amp the export left out

Run it straight after step-to-mesh.mjs and before add-thermistors.py: it only adds
meshes and edits existing ones in place, so the parts list stays valid for the next patch.
"""
import json, math, re, struct, sys
from pcb3 import read_model, write_model, add_mesh, colour_index, recolour
from kicad_sexpr import parse, kids, kid, val, prop
from geom import Mesh, box

BOARD_TOP = 1.6
FILM_GREY = (146, 148, 153)
IC_BODY, IC_LEAD, IC_DOT = (38, 37, 37), (200, 200, 200), (70, 70, 72)


def footprints(pcb_path):
    pcb = parse(open(pcb_path).read())
    out = []
    for fp in kids(pcb, "footprint"):
        at = kid(fp, "at"); x, y = float(at[1]), float(at[2]); a = float(at[3]) if len(at) > 3 else 0.0
        r = math.radians(a)
        to_world = lambda px, py, x=x, y=y, r=r: (x + px * math.cos(r) + py * math.sin(r), -(y - px * math.sin(r) + py * math.cos(r)))
        silk = []
        for ln in kids(fp, "fp_line"):
            if val(ln, "layer") not in ("F.SilkS", "F.Silkscreen"): continue
            s, e = kid(ln, "start"), kid(ln, "end")
            st = kid(ln, "stroke"); w = float(val(st, "width")) if st else float(val(ln, "width", default=0.12))
            silk.append(((float(s[1]), float(s[2])), (float(e[1]), float(e[2])), w))
        out.append({"ref": prop(fp, "Reference") or "", "lib": fp[1].split(":")[-1], "x": x, "y": -y, "rot": r,
                    "side": "top" if val(fp, "layer") == "F.Cu" else "bottom", "world": to_world, "silk": silk})
    return out


def placement(fp, z=0.0):
    c, s = math.cos(fp["rot"]), math.sin(fp["rot"])
    return [c, -s, 0, fp["x"], s, c, 0, fp["y"], 0, 0, 1, z]


def part_near(parts, x, y, min_size=2.0, tol=1.2):
    for p in parts:
        cx, cy = (p["lo"][0] + p["hi"][0]) / 2, (p["lo"][1] + p["hi"][1]) / 2
        size = max(p["hi"][0] - p["lo"][0], p["hi"][1] - p["lo"][1])
        if size >= min_size and math.hypot(cx - x, cy - y) < tol and "copper" not in p["name"] and "silk" not in p["name"] \
                and "pads" not in p["name"] and not p["name"].upper().endswith("PCB"):
            return p
    return None


def world_verts(mesh, M):
    """(chunk index, vertex index, world xyz) for every vertex of a mesh under matrix M."""
    for ci, (vc, ic, P, N, I) in enumerate(mesh["chunks"]):
        q = struct.unpack(f"<{vc * 3}H", P)
        for v in range(vc):
            l = [mesh["qmin"][k] + q[v * 3 + k] * mesh["qstep"][k] for k in range(3)]
            yield ci, v, [M[r * 4] * l[0] + M[r * 4 + 1] * l[1] + M[r * 4 + 2] * l[2] + M[r * 4 + 3] for r in range(3)]


def luma(c): return 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]


def place(m, mesh, mats):
    k = add_mesh(m, mesh.pos, mesh.nrm, mesh.col, mesh.tri, instance=False)
    for M in mats: m.instances.append((k, M, 0))


def film(m, parts, fps):
    grey = colour_index(m, FILM_GREY)
    added, regreyed = [], 0
    shapes = {}
    for fp in fps:
        g = re.search(r"C_Rect_L([\d.]+)mm_W([\d.]+)mm_P([\d.]+)mm", fp["lib"])
        if not g: continue
        L, W, P = map(float, g.groups())
        cx, cy = fp["world"](P / 2, 0)
        existing = part_near(parts, cx, cy)
        if existing:
            # KiCad's film-cap models are red; these are grey
            red = {i for i, c in enumerate(m.palette) if c[0] > 140 and c[1] < 90 and c[2] < 90}
            recolour(m, existing["mesh"], mapping={i: grey for i in red}); regreyed += 1
            continue
        H = 6.5 if L < 9 else 7.0
        key = (L, W, P)
        if key not in shapes: shapes[key] = []
        shapes[key].append(placement(fp, BOARD_TOP))
        added.append(fp["ref"])
    for (L, W, P), mats in shapes.items():
        H = 6.5 if L < 9 else 7.0
        body = box((P / 2 - L / 2, -W / 2, 0.15), (P / 2 + L / 2, W / 2, 0.15 + H), grey)
        lead = colour_index(m, IC_LEAD)
        for u in (0.0, P):                      # the two leads, visible under the body's standoff
            box((u - 0.25, -0.25, -0.05), (u + 0.25, 0.25, 0.3), lead, body)
        place(m, body, mats)
    print(f"  film caps: added {len(added)} ({' '.join(added) or '-'}), re-coloured {regreyed} red model(s) grey")


def plus_marks(fp):
    """Short crossing silkscreen strokes that make a '+' (in world coordinates)."""
    marks = []
    short = [(a, b, w) for a, b, w in fp["silk"] if math.dist(a, b) <= 1.3]
    for i, (a1, b1, w1) in enumerate(short):
        for a2, b2, w2 in short[i + 1:]:
            m1 = ((a1[0] + b1[0]) / 2, (a1[1] + b1[1]) / 2); m2 = ((a2[0] + b2[0]) / 2, (a2[1] + b2[1]) / 2)
            d1 = (b1[0] - a1[0], b1[1] - a1[1]); d2 = (b2[0] - a2[0], b2[1] - a2[1])
            perpendicular = abs(d1[0] * d2[0] + d1[1] * d2[1]) < 0.05 * math.hypot(*d1) * math.hypot(*d2)
            if perpendicular and math.dist(m1, m2) < 0.05:
                pts = [fp["world"](*p) for p in (a1, b1, a2, b2)]
                pad = max(w1, w2) / 2 + 0.06
                marks.append((min(p[0] for p in pts) - pad, min(p[1] for p in pts) - pad,
                              max(p[0] for p in pts) + pad, max(p[1] for p in pts) + pad))
    return marks


def bipolar(m, parts, fps, keep):
    caps = [fp for fp in fps if fp["lib"].startswith("CP_Elec") and fp["ref"] not in keep]
    meshes = {}
    for fp in caps:
        p = part_near(parts, fp["x"], fp["y"], min_size=3.0, tol=1.0)
        if p: meshes.setdefault(p["mesh"], p["m"])
    # 1) the band: dark faces on the can above its plastic base take the can's colour
    for k, M in meshes.items():
        verts = list(world_verts(m.meshes[k], M))
        zmin = min(w[2] for _, _, w in verts)
        counts = {}
        for ci, v, w in verts:
            c = m.meshes[k]["chunks"][ci][3][v * 4 + 3]; counts[c] = counts.get(c, 0) + 1
        can = max((c for c in counts if luma(m.palette[c]) > 150), key=lambda c: counts[c])
        chunks = []
        for ci, (vc, ic, P, N, I) in enumerate(m.meshes[k]["chunks"]):
            n = bytearray(N)
            chunks.append(n)
        for ci, v, w in verts:
            c = chunks[ci][v * 4 + 3]
            if luma(m.palette[c]) < 110 and w[2] > zmin + 1.3: chunks[ci][v * 4 + 3] = can
        m.meshes[k]["chunks"] = [(vc, ic, P, bytes(chunks[ci]), I) for ci, (vc, ic, P, N, I) in enumerate(m.meshes[k]["chunks"])]
    # 2) the "+" printed next to each cap
    boxes = [b for fp in caps for b in plus_marks(fp)]
    silk = [p for p in parts if p["name"] == "silkscreen top"]
    removed = 0
    if silk and boxes:
        k, M = silk[0]["mesh"], silk[0]["m"]
        mesh = m.meshes[k]
        inside = {}
        for ci, v, w in world_verts(mesh, M):
            inside[(ci, v)] = any(b[0] <= w[0] <= b[2] and b[1] <= w[1] <= b[3] for b in boxes)
        chunks = []
        for ci, (vc, ic, P, N, I) in enumerate(mesh["chunks"]):
            idx = struct.unpack(f"<{ic}H", I)
            keep_tris = [idx[t:t + 3] for t in range(0, ic, 3) if not all(inside[(ci, i)] for i in idx[t:t + 3])]
            removed += ic // 3 - len(keep_tris)
            flat = [i for t in keep_tris for i in t]
            chunks.append((vc, len(flat), P, N, struct.pack(f"<{len(flat)}H", *flat)))
        mesh["chunks"] = chunks
    print(f"  bipolar: {len(caps)} caps on {len(meshes)} model(s); {len(boxes)} '+' marks, {removed} silkscreen triangles removed"
          + (f"; kept polarized: {', '.join(sorted(keep))}" if keep else ""))


def soic(m, parts, fps, ref):
    fp = next(f for f in fps if f["ref"] == ref)
    g = re.search(r"SOIC-(\d+)_([\d.]+)x([\d.]+)mm", fp["lib"])
    pins, W, L = int(g.group(1)), float(g.group(2)), float(g.group(3))
    if part_near(parts, fp["x"], fp["y"], min_size=3.0):
        print(f"  {ref}: already has a model"); return
    body, lead, dot = colour_index(m, IC_BODY), colour_index(m, IC_LEAD), colour_index(m, IC_DOT)
    mesh = box((-W / 2, -L / 2, 0.15), (W / 2, L / 2, 1.65), body)
    n = pins // 2
    for i in range(n):
        v = (i - (n - 1) / 2) * 1.27
        for sx in (-1, 1):
            box((min(sx * W / 2, sx * 2.95), v - 0.21, 0.0), (max(sx * W / 2, sx * 2.95), v + 0.21, 0.22), lead, mesh)       # foot
            box((min(sx * W / 2, sx * 2.35), v - 0.21, 0.0), (max(sx * W / 2, sx * 2.35), v + 0.21, 0.95), lead, mesh)       # shoulder
    # pin-1 dimple (pin 1 is at KiCad (-x, -y), i.e. local (-u, +v))
    box((-W / 2 + 0.45, L / 2 - 1.25, 1.65), (-W / 2 + 1.05, L / 2 - 0.65, 1.67), dot, mesh)
    place(m, mesh, [placement(fp, BOARD_TOP)])
    print(f"  {ref}: added a SOIC-{pins} body")


def main(argv):
    pcb_path, bin_path, parts_path = argv[:3]
    opts = argv[3:]
    m = read_model(bin_path); parts = json.load(open(parts_path)); fps = footprints(pcb_path)
    m.instances = [(inst[0], inst[1], inst[2] if len(inst) > 2 else 0) for inst in m.instances]
    i = 0
    while i < len(opts):
        o = opts[i]
        if o == "--film": film(m, parts, fps)
        elif o == "--bipolar":
            i += 1; keep = set() if opts[i] == "-" else set(opts[i].split(","))
            bipolar(m, parts, fps, keep)
        elif o == "--soic":
            i += 1; soic(m, parts, fps, opts[i])
        else: sys.exit("unknown option " + o)
        i += 1
    write_model(m, bin_path)


if __name__ == "__main__":
    main(sys.argv[1:])
