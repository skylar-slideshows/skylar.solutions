#!/usr/bin/env python3
"""Finish a master-section board model after step-to-mesh.mjs (with --parts).

    python3 tools/finish-master.py MODEL.bin OUT.bin [--vcas DONOR.bin]

- drops the "plated holes and vias" part: the barrels sit inside holes that the converter
  fills (--fill-holes), so they can't be seen and only cost triangles
- gives the board and its copper the same green as the other boards
- --vcas DONOR.bin, for the master analog board: the export puts an AS2180-on-adapter
  breakout in each of the eight 1x08 sockets; these come out, and the THAT2180 SIP-8
  model from the fader VCA card (DONOR.bin, converted from FADER_VCA_card.step with
  --parts) goes into each socket instead, pin 1 to pin 1 with its pins down in the
  socket. Each of the four axial thermistor footprints becomes a bead thermistor whose
  leads bend it against the nearer VCA of its pair. The bead and lead geometry is
  add-thermistors.py's; the positions come from the parts list, since there is no KiCad
  file for this board here.
"""
import importlib.util, json, math, os, struct, sys
from pcb3 import read_model, write_model, add_mesh, colour_index, recolour, remove_instances, compact_palette, mesh_points

MASK_GREEN, COPPER_GREEN = (58, 89, 78), (86, 125, 90)

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
        donor_path = flags[flags.index("--vcas") + 1]
        donor = read_model(donor_path)
        sip = next(p for p in json.load(open(donor_path.rsplit(".", 1)[0] + ".parts.json")) if p["name"].startswith("SIP-8"))
        # copy the THAT2180's SIP-8 model over, mapping its colours into this palette
        remap, chunks = {}, []
        for vc, ic, P, N, I in donor.meshes[sip["mesh"]]["chunks"]:
            N = bytearray(N)
            for v in range(vc):
                ci = N[v * 4 + 3]
                if ci not in remap: remap[ci] = colour_index(model, donor.palette[ci])
                N[v * 4 + 3] = remap[ci]
            chunks.append((vc, ic, P, bytes(N), I))
        model.meshes.append(dict(donor.meshes[sip["mesh"]], chunks=chunks))
        k_sip = len(model.meshes) - 1
        # the package body in the model's own frame: pin 1 at the origin, pins along +x
        body = [q for ch in mesh_points(donor, sip["mesh"]) for q in ch if q[2] > 0.3]
        bx, by, bz = [(min(q[i] for q in body), max(q[i] for q in body)) for i in range(3)]

        sockets = [p for p in parts if "PinSocket_1x08" in p["name"]]
        layers = {"copper top", "copper bottom", "pads top", "pads bottom", "silkscreen top", "silkscreen bottom", "plated holes and vias"}
        removed = 0
        vcas = []
        for s_ in sockets:
            top = s_["hi"][2]
            # the AS2180 breakout standing in the socket: its adapter board, header, chip and capacitor
            for q in parts:
                if q is s_ or q is board or q["name"] in layers: continue
                c = centre(q)
                if all(s_["lo"][i] - 1.5 <= c[i] <= s_["hi"][i] + 1.5 for i in range(2)) and q["hi"][2] > top + 0.5:
                    i = instance_of(model, q)
                    if i is not None and i not in drop: drop.add(i); removed += 1
            ox, oy = s_["m"][3], s_["m"][7]                 # the socket's pin 1
            cx, cy = centre(s_)[:2]
            d = math.hypot(cx - ox, cy - oy)
            assert 8.0 < d < 9.8, ("pin 1 to socket centre", d)
            dx, dy = (cx - ox) / d, (cy - oy) / d
            mat = [dx, -dy, 0.0, ox, dy, dx, 0.0, oy, 0.0, 0.0, 1.0, top]
            model.instances.append((k_sip, mat, 0))
            world = [(mat[0] * x + mat[1] * y + ox, mat[4] * x + mat[5] * y + oy, z + top) for x in bx for y in by for z in bz]
            vcas.append({"lo": [min(w[i] for w in world) for i in range(3)], "hi": [max(w[i] for w in world) for i in range(3)]})
        print(f"  removed {removed} breakout parts; seated {len(vcas)} THAT2180s")

        colours = {rgb: colour_index(model, rgb) for *_, rgb in th.BANDS}
        lead_ci = colour_index(model, th.LEAD_COLOUR)
        pos, nrm, col, tri = [], [], [], []
        for q in [p for p in parts if "R_Axial" in p["name"]]:
            pads = pads_of(model, q, board_bottom)
            tx, ty = sum(p[0] for p in pads) / 2, sum(p[1] for p in pads) / 2
            mid = lambda v: ((v["lo"][0] + v["hi"][0]) / 2, (v["lo"][1] + v["hi"][1]) / 2)
            pair = sorted(vcas, key=lambda v: math.hypot(mid(v)[0] - tx, mid(v)[1] - ty))[:2]
            v = pair[0]                                  # bent against the nearer VCA of the pair
            thin = 0 if v["hi"][0] - v["lo"][0] < v["hi"][1] - v["lo"][1] else 1
            long_ = 1 - thin
            t = (tx, ty)
            n = [0.0, 0.0, 0.0]
            n[thin] = 1.0 if t[thin] > mid(v)[thin] else -1.0
            point = [0.0, 0.0, (v["lo"][2] + v["hi"][2]) / 2]
            point[thin] = v["hi"][thin] if n[thin] > 0 else v["lo"][thin]
            point[long_] = max(v["lo"][long_] + 2.0, min(v["hi"][long_] - 2.0, t[long_]))
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
            print(f"  thermistor at ({tx:.1f}, {ty:.1f}) -> VCA at ({mid(v)[0]:.1f}, {mid(v)[1]:.1f}), leads {L:.1f} mm")
        add_mesh(model, pos, nrm, col, tri)

    remove_instances(model, {i for i in drop if i is not None})
    compact_palette(model)
    write_model(model, out)
    drawn = sum(sum(ic // 3 for vc, ic, *_ in model.meshes[inst[0]]["chunks"]) for inst in model.instances)
    print(f"{out}: {drawn} triangles drawn, {len(model.palette)} colours")


if __name__ == "__main__":
    main(*sys.argv[1:])
