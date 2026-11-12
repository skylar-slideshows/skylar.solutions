#!/usr/bin/env python3
"""Finish a master-section board model after step-to-mesh.mjs (with --parts).

    python3 tools/finish-master.py MODEL.bin OUT.bin [--vcas]

- drops the "plated holes and vias" part: the barrels sit inside holes that the converter
  fills (--fill-holes), so they can't be seen and only cost triangles
- gives the board and its copper the same green as the other boards
- --vcas, for the master analog board: a THAT2180 SIP-8 VCA seated in each of the eight
  1x08 sockets, and each of the four axial thermistor footprints turned into a bead
  thermistor whose leads bend it against the nearer VCA of its pair. The bead and lead
  geometry is add-thermistors.py's; the positions come from the parts list, since there
  is no KiCad file for this board here.
"""
import importlib.util, json, math, os, struct, sys
from pcb3 import read_model, write_model, add_mesh, colour_index, recolour, remove_instances, compact_palette, mesh_points
from geom import box

MASK_GREEN, COPPER_GREEN = (58, 89, 78), (86, 125, 90)
VCA_BODY, VCA_LEAD = (32, 32, 35), (200, 200, 204)
SIP_L, SIP_T, SIP_H, SIP_LIFT = 19.6, 2.9, 6.3, 0.8      # THAT2180 SIP-8 body, and its leads' visible length

spec = importlib.util.spec_from_file_location("thermistors", os.path.join(os.path.dirname(os.path.abspath(__file__)), "add-thermistors.py"))
th = importlib.util.module_from_spec(spec); spec.loader.exec_module(th)


def centre(p):
    return [(p["lo"][k] + p["hi"][k]) / 2 for k in range(3)]


def instance_of(model, part):
    """Index of the model instance a parts-list entry describes (matrices are stored as float32)."""
    for i, (mesh, mat, *rest) in enumerate(model.instances):
        if mesh == part["mesh"] and all(abs(mat[k] - part["m"][k]) < 0.01 for k in (3, 7, 11)):
            return i
    return None


def pads_of(model, part, board_bottom):
    """The two lead ends under the board: where the footprint's pins are."""
    M, pts = part["m"], []
    for chunk in mesh_points(model, part["mesh"]):
        for x, y, z in chunk:
            w = [M[r * 4] * x + M[r * 4 + 1] * y + M[r * 4 + 2] * z + M[r * 4 + 3] for r in range(3)]
            if w[2] < board_bottom - 0.2: pts.append(w)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    k = 0 if max(xs) - min(xs) > max(ys) - min(ys) else 1
    mid = (min(p[k] for p in pts) + max(p[k] for p in pts)) / 2
    groups = [[p for p in pts if p[k] < mid], [p for p in pts if p[k] >= mid]]
    return [(sum(p[0] for p in g) / len(g), sum(p[1] for p in g) / len(g)) for g in groups]


def main(path, out, *flags):
    model = read_model(path)
    parts = json.load(open(path.rsplit(".", 1)[0] + ".parts.json"))
    board = next(p for p in parts if p["name"].upper().startswith("PCB") or p["name"].upper().endswith("PCB"))
    board_top, board_bottom = board["hi"][2], board["lo"][2]
    green, copper = colour_index(model, MASK_GREEN), colour_index(model, COPPER_GREEN)
    recolour(model, board["mesh"], to=green)
    for p in parts:
        if p["name"] in ("copper top", "copper bottom"): recolour(model, p["mesh"], to=copper)
    drop = {instance_of(model, p) for p in parts if p["name"] == "plated holes and vias"}

    if "--vcas" in flags:
        body, lead = colour_index(model, VCA_BODY), colour_index(model, VCA_LEAD)
        sockets = [p for p in parts if "PinSocket_1x08" in p["name"]]
        vcas = []
        m = None
        for s in sockets:
            cx, cy, _ = centre(s)
            along_x = s["hi"][0] - s["lo"][0] > s["hi"][1] - s["lo"][1]
            z0 = s["hi"][2] + SIP_LIFT
            hl, ht = SIP_L / 2, SIP_T / 2
            lo = (cx - hl, cy - ht, z0) if along_x else (cx - ht, cy - hl, z0)
            hi = (cx + hl, cy + ht, z0 + SIP_H) if along_x else (cx + ht, cy + hl, z0 + SIP_H)
            m = box(lo, hi, body, m)
            for i in range(8):                         # the leads between the socket and the body
                t = (i - 3.5) * 2.54
                lx, ly = (cx + t, cy) if along_x else (cx, cy + t)
                ex, ey = (0.25, 0.13) if along_x else (0.13, 0.25)
                box((lx - ex, ly - ey, s["hi"][2] - 0.4), (lx + ex, ly + ey, z0 + 0.2), lead, m)
            vcas.append({"c": (cx, cy), "along_x": along_x, "z0": z0})
        add_mesh(model, m.pos, m.nrm, m.col, m.tri)

        colours = {rgb: colour_index(model, rgb) for *_, rgb in th.BANDS}
        lead_ci = colour_index(model, th.LEAD_COLOUR)
        pos, nrm, col, tri = [], [], [], []
        for q in [p for p in parts if "R_Axial" in p["name"]]:
            pads = pads_of(model, q, board_bottom)
            tx, ty = sum(p[0] for p in pads) / 2, sum(p[1] for p in pads) / 2
            pair = sorted(vcas, key=lambda v: math.hypot(v["c"][0] - tx, v["c"][1] - ty))[:2]
            v = pair[0]                                  # bent against the nearer VCA of the pair
            # the broad face that looks toward the thermistor, at the point nearest it
            if v["along_x"]:
                n = [0.0, 1.0 if ty > v["c"][1] else -1.0, 0.0]
                u = max(-SIP_L / 2 + 2.0, min(SIP_L / 2 - 2.0, tx - v["c"][0]))
                point = [v["c"][0] + u, v["c"][1] + n[1] * SIP_T / 2, v["z0"] + SIP_H / 2]
            else:
                n = [1.0 if tx > v["c"][0] else -1.0, 0.0, 0.0]
                u = max(-SIP_L / 2 + 2.0, min(SIP_L / 2 - 2.0, ty - v["c"][1]))
                point = [v["c"][0] + n[0] * SIP_T / 2, v["c"][1] + u, v["z0"] + SIP_H / 2]
            axis = [0.0, 0.0, 1.0]                     # leads come up from below
            a, b, c = th.BEAD
            centre_ = th.add(point, th.mul(n, c + 0.05))
            base = th.add(centre_, th.mul(axis, -a * 0.92))
            th.bead(centre_, axis, n, colours, pos, nrm, col, tri, size=(a, b, c))
            w = th.norm(th.cross(axis, n))
            ends = sorted([th.add(base, th.mul(w, 0.55)), th.add(base, th.mul(w, -0.55))], key=lambda e: th.dot(e, w))
            for (px, py), end in zip(sorted(pads, key=lambda p: th.dot([p[0], p[1], 0], w)), ends):
                under = [px, py, board_bottom - 1.0]
                exitp = [px, py, board_top + 1.2]
                L = math.dist(exitp, end)
                curve = th.bezier(exitp, th.add(exitp, [0, 0, min(L * 0.3, 3.0)]), th.add(end, th.mul(axis, -min(L * 0.35, 4.0))), end, n=th.RES["curve"])
                th.tube([under] + curve, lead_ci, pos, nrm, col, tri)
            drop.add(instance_of(model, q))
            print(f"  thermistor at ({tx:.1f}, {ty:.1f}) -> VCA at ({v['c'][0]:.1f}, {v['c'][1]:.1f}), leads {L:.1f} mm")
        add_mesh(model, pos, nrm, col, tri)
        print(f"  {len(vcas)} THAT2180s seated")

    remove_instances(model, {i for i in drop if i is not None})
    compact_palette(model)
    write_model(model, out)
    drawn = sum(sum(ic // 3 for vc, ic, *_ in model.meshes[inst[0]]["chunks"]) for inst in model.instances)
    print(f"{out}: {drawn} triangles drawn, {len(model.palette)} colours")


if __name__ == "__main__":
    main(*sys.argv[1:])
