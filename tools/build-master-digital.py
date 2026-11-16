#!/usr/bin/env python3
"""Master digital section: the master controls board under its front panel.

    python3 tools/build-master-digital.py MASTER_CONTROLS.bin ring.bin master_front_panel.dxf master-silkscreen.svg out.bin

MASTER_CONTROLS.bin and ring.bin come from step-to-mesh.mjs with --parts. The panel is
250 mm wide over the 230 mm board, 10 mm spare on each side for bracing; where it sits
comes from matching its encoder holes to the board's encoder shafts. As on the channel
strips, LED ring boards plug into the board's 1x04 sockets under the four ring windows,
the panel (1.6 mm, black anodized) sits 0.15 mm above them, and the Nidec caps are redrawn
to stand 3 mm through it. The meter cutouts get unlit LEDs: per meter, top to bottom,
one red, three yellow and seven green; the two master meters' LEDs have a white surround.
The five encoders get the channel strips' knob with a white cap (their shafts cut to fit
under it, as there), and the five transport buttons carry their symbols on the caps
instead of the labels under them: a black square (STOP), triangle (PLAY), double
triangles (REW, FF) and a red circle (REC).

groups: 0  board, parts and LED ring boards
        1  front panel: the panel, its silkscreen, the display glass, the meter LEDs and the knobs
"""
import importlib.util, math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pcb3 import read_model, write_model, colour_index, recolour, remove_instances, compact_palette, translate, mesh_points
from geom import Mesh, box, extrude, rrect
import silkscreen

spec = importlib.util.spec_from_file_location("build_controls", os.path.join(HERE, "build-controls.py"))
bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)

METER_OFF = [(112, 36, 34)] + [(128, 112, 44)] * 3 + [(46, 98, 52)] * 7     # unlit lenses, top to bottom
LED_SURROUND = (236, 236, 230)
KNOB_WHITE = (240, 240, 236)
SYMBOL_BLACK, SYMBOL_RED = (24, 24, 26), (210, 34, 38)
TRANSPORT = ("STOP", "PLAY", "REC", "REW", "FF")          # the labels the symbols replace


def symbol(m, name, z):
    """A transport symbol printed on a 12 mm cap top, centred on the cap."""
    tri = lambda x0, x1, h: [(x0, -h), (x1, 0.0), (x0, h)]
    shapes = {"STOP": [[(-2.2, -2.2), (2.2, -2.2), (2.2, 2.2), (-2.2, 2.2)]],
              "PLAY": [tri(-2.1, 2.4, 2.6)],
              "FF": [tri(-2.4, 0.0, 2.2), tri(0.0, 2.4, 2.2)],
              "REW": [[(-x, y) for x, y in tri(-2.4, 0.0, 2.2)][::-1], [(-x, y) for x, y in tri(0.0, 2.4, 2.2)][::-1]],
              "REC": [[(2.5 * math.cos(a * math.pi / 24), 2.5 * math.sin(a * math.pi / 24)) for a in range(48)]]}
    ci = colour_index(m, SYMBOL_RED if name == "REC" else SYMBOL_BLACK)
    out = None
    for poly in shapes[name]:
        piece = extrude(poly, [], z, z + 0.03, ci)
        out = piece if out is None else (out.add(piece) or out)
    return out


def shaft_axis(model, part):
    """Where an encoder's shaft axis is in x, y: the centre of its round threaded bushing.
    (Not the shaft's tip: that is D-shaped, so an average of its points sits about 1.1 mm
    toward the round side.)"""
    M, levels = part["m"], {}
    pts = [[M[r * 4] * x + M[r * 4 + 1] * y + M[r * 4 + 2] * z + M[r * 4 + 3] for r in range(3)]
           for chunk in mesh_points(model, part["mesh"]) for x, y, z in chunk]
    top = max(p[2] for p in pts)
    for p in pts:
        if p[2] > top - 20.0: levels.setdefault(round(p[2], 1), []).append(p)
    def size(ring):
        return min(max(q[0] for q in ring) - min(q[0] for q in ring), max(q[1] for q in ring) - min(q[1] for q in ring))
    ring = max(levels.values(), key=size)               # the widest round level: the bushing
    return ((max(q[0] for q in ring) + min(q[0] for q in ring)) / 2, (max(q[1] for q in ring) + min(q[1] for q in ring)) / 2)


def nidec_large(m, cap_top):
    """Nidec CFPB with the 12 mm cap: the same body as the small one."""
    body, cap = colour_index(m, bc.SWITCH_BODY), colour_index(m, bc.SWITCH_CAP)
    out = box((-4.95, -4.95, 1.6), (4.95, 4.95, 10.4), body)
    box((-4.1, -4.1, 10.4), (4.1, 4.1, 11.0), body, out)
    out.add(extrude(rrect(0, 0, 12.0, 12.0, 0.8), [], 11.0, cap_top - 0.35, cap))
    out.add(extrude(rrect(0, 0, 11.4, 11.4, 0.6), [], cap_top - 0.35, cap_top, cap))
    return out


def meters(layers):
    """The meter cutouts as columns of eleven, top first: (x, y, w, h) in panel mm."""
    cuts = []
    for loop in bc.chain(layers["LED_CUTOUT"]["paths"]):
        xs, ys = [p[0] for p in loop], [p[1] for p in loop]
        cuts.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(xs) - min(xs), max(ys) - min(ys)))
    out = []
    for x in sorted({round(c[0], 1) for c in cuts}):
        col = sorted((c for c in cuts if abs(c[0] - x) < 0.15), key=lambda c: -c[1])
        pitch = min(a[1] - b[1] for a, b in zip(col, col[1:]))
        run = [col[0]]
        for a, b in zip(col, col[1:]):
            if a[1] - b[1] > 1.5 * pitch:
                out.append(run); run = []
            run.append(b)
        out.append(run)
    assert all(len(r) == 11 for r in out), [len(r) for r in out]
    return out


def main(ctl_path, ring_path, dxf_path, svg_path, out_path):
    layers = bc.read_dxf(dxf_path)
    asm = read_model(ctl_path); parts = bc.parts_of(ctl_path)

    # where the panel sits: its encoder holes over the board's encoder shafts
    shafts = [shaft_axis(asm, p) for p in parts if "EC11" in p["name"]]
    holes = [(x, y) for x, y, r in layers["ENCODER"]["circles"]]
    best = None
    for sx, sy in shafts:
        for hx, hy in holes:
            dx, dy = sx - hx, sy - hy
            err = max(min(math.hypot(x + dx - a, y + dy - b) for a, b in shafts) for x, y in holes)
            if best is None or err < best[0]: best = (err, dx, dy)
    err, PX, PY = best
    assert err < 0.6, ("encoders don't line up", err)
    board = next(p for p in parts if p["name"].startswith("PCB"))
    print(f"panel at ({PX:.2f}, {PY:.2f}): encoders within {err:.2f} mm; panel x {PX:.1f}..{PX + 250:.1f} "
          f"over the board's {board['lo'][0]:.1f}..{board['hi'][0]:.1f}")

    # LED ring boards on the four encoders under ring windows, plugged into the 1x04 sockets
    windows = [((min(p[0] for p in l) + max(p[0] for p in l)) / 2, (min(p[1] for p in l) + max(p[1] for p in l)) / 2)
               for l in bc.chain(layers["LED_RING"]["paths"])]
    ring_at = [min(shafts, key=lambda s: math.hypot(s[0] - wx - PX, s[1] - wy - PY)) for wx, wy in windows]
    socket_top = max(p["hi"][2] for p in parts if "PinSocket_1x04" in p["name"])
    ring_z = socket_top + bc.HEADER_BODY
    ring = read_model(ring_path); ring_parts = bc.parts_of(ring_path)
    hidden = bc.mesh_ids(ring_parts, "copper top", "copper bottom", "pads bottom", "silkscreen bottom", "TSSOP")
    ring_top = max(p["hi"][2] for p in ring_parts if p["mesh"] not in hidden) + ring_z
    sockets = [((p["lo"][0] + p["hi"][0]) / 2, (p["lo"][1] + p["hi"][1]) / 2) for p in parts if "PinSocket_1x04" in p["name"]]
    headers = [((p["lo"][0] + p["hi"][0]) / 2 - bc.RING_CENTRE[0], (p["lo"][1] + p["hi"][1]) / 2 - bc.RING_CENTRE[1])
               for p in ring_parts if "1x04" in p["name"]]
    miss = max(min(math.hypot(x + hx - a, y + hy - b) for a, b in sockets) for x, y in ring_at for hx, hy in headers)
    print(f"ring boards: {len(ring_at)}, centred on their encoders; their headers land within {miss:.2f} mm "
          f"of the board's sockets (0.10 mm on the channel strips)")

    cut = [((min(p[0] for p in l) + max(p[0] for p in l)) / 2 + PX, (min(p[1] for p in l) + max(p[1] for p in l)) / 2 + PY)
           for name in ("BUTTON", "BUTTON_LARGE") for l in bc.chain(layers[name]["paths"])]
    caps = [((p["lo"][0] + p["hi"][0]) / 2, (p["lo"][1] + p["hi"][1]) / 2) for p in parts if "CFPB" in p["name"]]
    off = max(min(math.hypot(cx - x, cy - y) for x, y in cut) for cx, cy in caps)
    print(f"buttons: every cap within {off:.2f} mm of its cutout's centre")
    panel_z0 = ring_top + 0.15
    panel_z1 = panel_z0 + 1.6
    nidec_top = panel_z1 + 3.0

    # the board: green like the others; the hidden via barrels and the Nidec switches go
    green, copper = colour_index(asm, bc.MASK_GREEN), colour_index(asm, bc.COPPER_GREEN)
    recolour(asm, board["mesh"], to=green)
    for k in bc.mesh_ids(parts, "copper top", "copper bottom"): recolour(asm, k, to=copper)
    small = [((p["lo"][0] + p["hi"][0]) / 2, (p["lo"][1] + p["hi"][1]) / 2) for p in parts if "CFPB-1CC-4W6" in p["name"]]
    large = [((p["lo"][0] + p["hi"][0]) / 2, (p["lo"][1] + p["hi"][1]) / 2) for p in parts if "CFPB-1CC-4W2" in p["name"]]
    gone = bc.mesh_ids(parts, "CFPB", "EC11", "plated holes and vias")
    asm.instances = [(mesh, mat, 0) for mesh, mat, *_ in asm.instances]
    remove_instances(asm, {i for i, inst in enumerate(asm.instances) if inst[0] in gone})
    bc.place(asm, bc.nidec_switch(asm, nidec_top), [translate(x, y, 0) for x, y in small], 0)
    bc.place(asm, nidec_large(asm, nidec_top), [translate(x, y, 0) for x, y in large], 0)
    knob_base = panel_z1 + 0.6
    bc.place(asm, bc.encoder(asm, knob_base + bc.KNOB_H - 0.6), [translate(x, y, 0) for x, y in shafts], 0)

    # the transport symbols, each on the cap whose label sits under it
    labels = {t["text"]: (t["x"] + t["width"] / 2, 500.0 - t["y"]) for t in silkscreen.parse_all(svg_path)[0] if t["text"] in TRANSPORT}
    for name, (lx, ly) in labels.items():
        bx, by = min(large, key=lambda b: abs(b[0] - PX - lx) + abs(b[1] - PY - ly - 10.0))
        assert abs(bx - PX - lx) < 1.0, (name, bx - PX, lx)
        bc.place(asm, symbol(asm, name, nidec_top), [translate(bx, by, 0)], 0)
    print("transport symbols:", ", ".join(f"{n} at x {labels[n][0]:.1f}" for n in TRANSPORT if n in labels))
    bc.add_instanced(asm, ring, [translate(x - bc.RING_CENTRE[0], y - bc.RING_CENTRE[1], ring_z) for x, y in ring_at], 0,
                     lambda i: i not in hidden)

    # the front panel and everything that goes with it (group 1)
    at = [translate(PX, PY, 0)]
    bc.place(asm, bc.tint(bc.faceplate(layers, panel_z0, panel_z1), colour_index(asm, bc.ANODIZED)), at, 1)
    silk, n_text, n_marks = silkscreen.mesh(svg_path, {"white": colour_index(asm, bc.SILK_WHITE), "grey": colour_index(asm, bc.SILK_GREY)},
                                            panel_z1 + 0.012, skip={"label_" + n for n in TRANSPORT})
    bc.place(asm, silk, at, 1)
    d = [p for path in layers["DISPLAY"]["paths"] for p in path]
    x0, x1 = min(p[0] for p in d) - 0.8, max(p[0] for p in d) + 0.8
    y0, y1 = min(p[1] for p in d) - 0.8, max(p[1] for p in d) + 0.8
    disp = box((x0, y0, panel_z0 - 1.35), (x1, y1, panel_z0 - 0.2), colour_index(asm, bc.GLASS))
    box((x0 - 1.5, y0 - 3.5, panel_z0 - 3.0), (x1 + 1.5, y1 + 1.5, panel_z0 - 1.35), colour_index(asm, bc.BEZEL), disp)
    bc.place(asm, disp, at, 1)

    leds, top = Mesh(), panel_z1 - 0.25
    surround = colour_index(asm, LED_SURROUND)
    found = meters(layers)
    for meter in found:
        for i, (cx, cy, w, h) in enumerate(meter):
            lens = colour_index(asm, METER_OFF[i])
            if w > 6.0:        # master meters: a white surround around the lens
                box((cx - w / 2 + 0.15, cy - h / 2 + 0.15, panel_z0 - 2.5), (cx + w / 2 - 0.15, cy + h / 2 - 0.15, top), surround, leds)
                box((cx - 2.5, cy - 1.25, top - 0.6), (cx + 2.5, cy + 1.25, top + 0.05), lens, leds)
            else:
                box((cx - w / 2 + 0.12, cy - h / 2 + 0.12, panel_z0 - 2.5), (cx + w / 2 - 0.12, cy + h / 2 - 0.12, top), lens, leds)
    bc.place(asm, leds, at, 1)
    bc.place(asm, bc.knob(asm, bc.KNOB_H), [translate(x, y, knob_base) for x, y in shafts], 1)
    bc.place(asm, bc.knob_cap(asm, bc.KNOB_H, KNOB_WHITE), [translate(x, y, knob_base) for x, y in shafts], 1)
    print(f"meters: {len(found)} ({sum(1 for m in found if m[0][2] > 6.0)} master), {sum(len(m) for m in found)} LEDs; "
          f"buttons {len(small)} small + {len(large)} large; silkscreen {n_text} labels, {n_marks} marks")

    compact_palette(asm)
    write_model(asm, out_path)
    drawn = {}
    for mesh, mat, g in asm.instances:
        drawn[g] = drawn.get(g, 0) + sum(ic // 3 for vc, ic, *_ in asm.meshes[mesh]["chunks"])
    print(f"panel {panel_z0:.2f}..{panel_z1:.2f} mm; colours {len(asm.palette)}; triangles drawn by group {drawn} (total {sum(drawn.values())})")


if __name__ == "__main__":
    main(*sys.argv[1:6])
