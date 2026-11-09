#!/usr/bin/env python3
"""Build the Digilog controls assembly as one viewer model.

    python3 tools/build-controls.py controls.bin fader.bin ring.bin faceplate.dxf out.bin

controls.bin / fader.bin / ring.bin come from step-to-mesh.mjs (with --parts, so a
NAME.parts.json sits next to each). The assembly is:

  group 0  controls board + fader board (green mask) and 4 ALPS RSA0N11M9 motor faders
  group 1  40 LED ring boards (black mask) plugged into their sockets
  group 2  4 black anodized faceplate strips from the DXF, knob caps on the
           encoder shafts and the display glass behind the panel windows

Groups 1 and 2 are the viewer's show/hide checkboxes. Positions come from the
faceplate: panel (x, y) sits over controls-board KiCad (100.1 + x, 580 - y), strips
36.5 mm apart, and the fader board's KiCad origin is offset (+70, +455) from the
controls board's (found from its M2 mounting holes).
"""
import json, math, sys
from pcb3 import read_model, write_model, add_mesh, colour_index, recolour, mat_mul, translate
from geom import Mesh, box, extrude, lathe, cylinder_y, rrect, circle, arc_points

STRIP_X0, STRIP_PITCH, PANEL_Y0 = 100.1, 36.5, 580.0
FADER_BOARD = (70.0, -455.0)          # fader board -> assembly, in STEP (y-up) coordinates
RING_CENTRE = (130.0, -60.0)          # LED ring centre in its own STEP coordinates
SOCKET_TOP = 9.92                     # top of the 8.5 mm 1x04 sockets on the controls board
HEADER_BODY = 2.54                    # plastic body of the ring's 1x04 header
RING_Z = SOCKET_TOP + HEADER_BODY     # ring board underside
SHAFT_TOP = 32.92                     # top of the EC11 25 mm shafts
FADER_MTG = 7.472                     # fader mounting surface, as placed on the fader board design:
                                      # the bodies drop through the board's four slots
LEVER_TOP = 18.5                      # lever tip above the mounting surface (ALPS drawing)

MASK_GREEN, COPPER_GREEN = (58, 89, 78), (86, 125, 90)   # same as the analog cards
ANODIZED = (34, 34, 38)
KNOB, KNOB_LINE = (31, 31, 34), (238, 238, 232)
FRAME_TOP, FRAME_SIDE, FADER_PCB = (204, 205, 210), (168, 169, 174), (92, 66, 48)
MOTOR_CAN, MOTOR_END, GEARBOX = (192, 194, 200), (54, 54, 58), (42, 42, 46)
GLASS, BEZEL = (16, 18, 26), (40, 40, 44)


def parts_of(path):
    return json.load(open(path.rsplit('.', 1)[0] + '.parts.json'))


def mesh_ids(parts, *names):
    return {p['mesh'] for p in parts if any(n.lower() in p['name'].lower() for n in names)}


def add_instanced(dst, src, transforms, group, keep_mesh=lambda i: True):
    """Copy src's meshes once and place them at every transform."""
    pal = {i: colour_index(dst, c) for i, c in enumerate(src.palette)}
    used = sorted({mesh for mesh, *_ in src.instances if keep_mesh(mesh)})
    remap = {}
    for old in used:
        mesh = dict(src.meshes[old]); chunks = []
        for vc, ic, P, N, I in mesh['chunks']:
            n = bytearray(N)
            for v in range(vc): n[v * 4 + 3] = pal[n[v * 4 + 3]]
            chunks.append((vc, ic, P, bytes(n), I))
        mesh['chunks'] = chunks
        remap[old] = len(dst.meshes); dst.meshes.append(mesh)
    for T in transforms:
        for mesh, mat, *_ in src.instances:
            if mesh in remap: dst.instances.append((remap[mesh], mat_mul(T, mat), group))


def place(dst, mesh, transforms, group):
    """Store a generated Mesh once and instance it."""
    k = add_mesh(dst, mesh.pos, mesh.nrm, mesh.col, mesh.tri, group=group, instance=False)
    for T in transforms: dst.instances.append((k, T, group))


def rot_z(a, x, y, z):
    c, s = math.cos(a), math.sin(a)
    return [c, -s, 0, x, s, c, 0, y, 0, 0, 1, z]


# ---------------------------------------------------------------------------
# faceplate DXF
# ---------------------------------------------------------------------------
def read_dxf(path):
    lines = open(path, errors='replace').read().splitlines()
    pairs = [(lines[i].strip(), lines[i + 1].strip()) for i in range(0, len(lines) - 1, 2)]
    ents, cur = [], None
    for c, v in pairs:
        if c == '0':
            if cur: ents.append(cur)
            cur = {'type': v} if v in ('LINE', 'ARC', 'CIRCLE') else None
        elif cur is not None:
            cur.setdefault(c, []).append(v)
    if cur: ents.append(cur)
    f = lambda e, k: float(e[k][0])
    layers = {}
    for e in ents:
        L = layers.setdefault(e['8'][0], {'paths': [], 'circles': []})
        if e['type'] == 'LINE':
            L['paths'].append([(f(e, '10'), f(e, '20')), (f(e, '11'), f(e, '21'))])
        elif e['type'] == 'ARC':
            L['paths'].append(arc_points(f(e, '10'), f(e, '20'), f(e, '40'), math.radians(f(e, '50')), math.radians(f(e, '51')), 12.0))
        else:
            L['circles'].append((f(e, '10'), f(e, '20'), f(e, '40')))
    return layers


def chain(paths, tol=0.01):
    """Join line/arc pieces end to end into closed loops."""
    close = lambda a, b: abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol
    paths, loops = [list(p) for p in paths], []
    while paths:
        loop = paths.pop(0)
        while not close(loop[0], loop[-1]):
            for i, p in enumerate(paths):
                if close(loop[-1], p[0]): loop += p[1:]; break
                if close(loop[-1], p[-1]): loop += p[::-1][1:]; break
            else:
                raise ValueError(f"open outline near {loop[-1]}")
            paths.pop(i)
        loops.append(loop[:-1])
    return loops


def faceplate(layers, z0, z1):
    outer = chain(layers['OUTLINE']['paths'])[0]
    holes = []
    for name, L in layers.items():
        if name == 'OUTLINE': continue
        if L['paths']: holes += chain(L['paths'])
        for cx, cy, r in L['circles']: holes.append(circle(cx, cy, r, 20 if r > 2 else 12))
    return extrude(outer, holes, z0, z1, 0)


# ---------------------------------------------------------------------------
# generated parts (local coordinates)
# ---------------------------------------------------------------------------
def tint(mesh, ci):
    mesh.col = [ci] * len(mesh.pos)
    return mesh


def knob(m, r, height):
    """Knob cap: slightly tapered body, chamfered top, white pointer line."""
    body = lathe([(r + 0.25, 0.0), (r + 0.25, 1.0), (r, height - 0.9), (r - 0.9, height), (0.0, height)], colour_index(m, KNOB), 40)
    line = colour_index(m, KNOB_LINE)
    body.add(box((-0.35, 1.2, height - 0.04), (0.35, r - 0.55, height + 0.04), line))   # across the top
    body.add(box((-0.35, r - 0.35, 1.6), (0.35, r + 0.32, height - 0.95), line))       # down the side
    return body


def fader_body(m, motor_end):
    """ALPS RSA0N11M9 (100 mm motor fader), mounting surface at z = 0, length along y.
    Sizes from ALPS drawing SA01RM923: 144 mm frame, M3 holes 120 mm apart, 13.8 mm
    below the mounting surface to the PCB, 21 mm max with the motor."""
    s = motor_end
    top = extrude(rrect(0, 0, 13.0, 144.0, 2.0),
                  [rrect(0, 0, 1.6, 104.0, 0.8), circle(0, 60, 1.6, 16), circle(0, -60, 1.6, 16)],
                  -0.8, 0.0, colour_index(m, FRAME_TOP))
    out = Mesh().add(top)
    box((-5.3, -64.0, -11.8), (5.3, 64.0, -0.8), colour_index(m, FRAME_SIDE), out)       # frame channel
    box((-1.2, -52.0, -0.9), (1.2, 52.0, -0.7), colour_index(m, GEARBOX), out)           # dark track under the slot
    box((-5.0, -62.0, -13.8), (5.0, 62.0, -11.8), colour_index(m, FADER_PCB), out)       # resistor board + terminals
    y0, y1 = sorted((s * 11.0, s * 38.4))
    out.add(cylinder_y(0.0, -15.0, 6.0, y0 + 1.6, y1 - 1.6, colour_index(m, MOTOR_CAN), 24))
    out.add(cylinder_y(0.0, -15.0, 5.6, y0, y0 + 1.6, colour_index(m, MOTOR_END), 24))
    out.add(cylinder_y(0.0, -15.0, 5.6, y1 - 1.6, y1, colour_index(m, MOTOR_END), 24))
    g0, g1 = sorted((s * 52.0, s * 69.0))
    box((-4.6, g0, -16.0), (4.6, g1, -11.8), colour_index(m, GEARBOX), out)              # belt pulley housing
    return out


def fader_knob(m, cap_bottom):
    """Lever and cap above the panel (cap_bottom is relative to the mounting surface)."""
    out = box((-0.6, -4.5, -2.0), (0.6, 4.5, LEVER_TOP), colour_index(m, FRAME_TOP))
    out.add(extrude(rrect(0, 0, 12.0, 22.0, 2.4), [], cap_bottom, cap_bottom + 10.5, colour_index(m, KNOB)))
    box((-6.04, -0.45, cap_bottom + 1.0), (6.04, 0.45, cap_bottom + 10.54), colour_index(m, KNOB_LINE), out)
    return out


def main(ctl_path, fad_path, ring_path, dxf_path, out_path):
    asm = read_model(ctl_path); ctl_parts = parts_of(ctl_path)
    green, copper = colour_index(asm, MASK_GREEN), colour_index(asm, COPPER_GREEN)
    for k in mesh_ids(ctl_parts, '_PCB'): recolour(asm, k, to=green)
    for k in mesh_ids(ctl_parts, 'copper top', 'copper bottom'): recolour(asm, k, to=copper)
    asm.instances = [(mesh, mat, 0) for mesh, mat, *_ in asm.instances]

    # fader board, without its placeholder faders
    fad = read_model(fad_path); fad_parts = parts_of(fad_path)
    fg, fc = colour_index(fad, MASK_GREEN), colour_index(fad, COPPER_GREEN)
    for k in mesh_ids(fad_parts, '_PCB'): recolour(fad, k, to=fg)
    for k in mesh_ids(fad_parts, 'copper top', 'copper bottom'): recolour(fad, k, to=fc)
    placeholder = mesh_ids(fad_parts, 'RSA0N11')
    add_instanced(asm, fad, [translate(FADER_BOARD[0], FADER_BOARD[1], 0)], 0, lambda i: i not in placeholder)

    # panel geometry decides where everything else goes
    layers = read_dxf(dxf_path)
    enc = [(STRIP_X0 + STRIP_PITCH * s + x, y - PANEL_Y0) for s in range(4) for x, y, r in layers['ENCODER']['circles']]
    assert len(enc) == 40

    # LED rings: only what shows from above (copper under black mask and the underside are skipped)
    ring = read_model(ring_path); ring_parts = parts_of(ring_path)
    hidden = mesh_ids(ring_parts, 'copper top', 'copper bottom', 'pads bottom', 'silkscreen bottom', 'TSSOP')
    ring_top = max(p['hi'][2] for p in ring_parts if p['mesh'] not in hidden) + RING_Z
    add_instanced(asm, ring, [translate(x - RING_CENTRE[0], y - RING_CENTRE[1], RING_Z) for x, y in enc], 1,
                  lambda i: i not in hidden)

    panel_z0 = ring_top + 0.15                # floats just above the rings
    panel_z1 = panel_z0 + 1.6

    # faceplate strips (group 2)
    plate = tint(faceplate(layers, panel_z0, panel_z1), colour_index(asm, ANODIZED))
    place(asm, plate, [translate(STRIP_X0 + STRIP_PITCH * s, -PANEL_Y0, 0) for s in range(4)], 2)

    # knob caps on the encoder shafts (group 2): pointer angles vary a little
    kn = knob(asm, 6.3, SHAFT_TOP + 0.8 - (panel_z1 + 0.6))
    angles = [math.radians(a) for a in (-120, -60, 0, 45, 100, -30, 75, -90, 20, 135)]
    place(asm, kn, [rot_z(-angles[i % 10], x, y, panel_z1 + 0.6) for i, (x, y) in enumerate(enc)], 2)

    # display glass behind each window (group 2)
    dx = [p for path in layers['DISPLAY']['paths'] for p in path]
    x0, x1 = min(p[0] for p in dx) - 0.8, max(p[0] for p in dx) + 0.8
    y0, y1 = min(p[1] for p in dx) - 0.8, max(p[1] for p in dx) + 0.8
    disp = box((x0, y0, panel_z0 - 1.35), (x1, y1, panel_z0 - 0.2), colour_index(asm, GLASS))
    box((x0 - 1.5, y0 - 3.5, panel_z0 - 3.0), (x1 + 1.5, y1 + 1.5, panel_z0 - 1.35), colour_index(asm, BEZEL), disp)
    place(asm, disp, [translate(STRIP_X0 + STRIP_PITCH * s, -PANEL_Y0, 0) for s in range(4)], 2)

    # motor faders (group 0): bodies down through the fader board's slots, lever up through
    # the panel; the motor sits at the end over the fader board's spine (-y)
    fx = [STRIP_X0 + STRIP_PITCH * s + 18.15 for s in range(4)]
    fy = 86.0 - PANEL_Y0                     # slot centre (between the fader screws)
    place(asm, fader_body(asm, -1), [translate(x, fy, FADER_MTG) for x in fx], 0)
    travel = (0.62, 0.48, 0.74, 0.55)        # lever positions, 0 = bottom of the slot
    lever = fader_knob(asm, panel_z1 + 1.2 - FADER_MTG)
    place(asm, lever, [translate(x, fy + (t - 0.5) * 100.0, FADER_MTG) for x, t in zip(fx, travel)], 0)

    write_model(asm, out_path)
    drawn = {}
    for mesh, mat, g in asm.instances:
        tris = sum(ic // 3 for vc, ic, *_ in asm.meshes[mesh]['chunks'])
        drawn[g] = drawn.get(g, 0) + tris
    print(f"panel {panel_z0:.2f}..{panel_z1:.2f} mm; colours {len(asm.palette)}; triangles drawn by group {drawn} "
          f"(total {sum(drawn.values())})")


if __name__ == '__main__':
    main(*sys.argv[1:6])
