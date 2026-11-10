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
from pcb3 import read_model, write_model, add_mesh, colour_index, recolour, mat_mul, translate, deform, mesh_points, remove_instances, compact_palette
from geom import Mesh, box, extrude, lathe, cylinder_y, rrect, circle, arc_points

STRIP_X0, STRIP_PITCH, PANEL_Y0 = 100.1, 36.5, 580.0
FADER_BOARD = (70.0, -455.0)          # fader board -> assembly, in STEP (y-up) coordinates
RING_CENTRE = (130.0, -60.0)          # LED ring centre in its own STEP coordinates
SOCKET_TOP = 9.92                     # top of the 8.5 mm 1x04 sockets on the controls board
HEADER_BODY = 2.54                    # plastic body of the ring's 1x04 header
RING_Z = SOCKET_TOP + HEADER_BODY     # ring board underside
SHAFT_TOP = 32.92                     # top of the EC11 25 mm shafts
LEVER_IN_CAP = 5.0                    # how far the fader lever reaches into its cap

MASK_GREEN, COPPER_GREEN = (58, 89, 78), (86, 125, 90)   # same as the analog cards
ANODIZED = (34, 34, 38)
KNOB, KNOB_GROOVE, KNOB_TOP = (34, 34, 37), (22, 22, 24), (240, 240, 236)
# cap colours per strip, top knob first: red, a group of eight in pairs, purple next to the fader
CAP_COLOURS = [(214, 43, 48), (240, 128, 34), (240, 128, 34), (245, 198, 44), (245, 198, 44),
               (64, 168, 84), (64, 168, 84), (52, 112, 214), (52, 112, 214), (132, 74, 204)]
FADER_CAP = (200, 202, 208)
SWITCH_BODY, SWITCH_CAP = (40, 40, 44), (240, 240, 236)
ENC_BASE, ENC_METAL, ENC_SHAFT = (46, 70, 58), (192, 193, 198), (212, 213, 217)
PEM_ZINC, PEM_BORE = (184, 185, 182), (64, 64, 66)
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


def disk(cx, cy, r, z, ci, segs=32, m=None):
    m = m or Mesh()
    c = m.vert((cx, cy, z), (0, 0, 1), ci)
    rim = [m.vert((cx + r * math.cos(2 * math.pi * k / segs), cy + r * math.sin(2 * math.pi * k / segs), z), (0, 0, 1), ci)
           for k in range(segs)]
    for k in range(segs): m.tri += [c, rim[k], rim[(k + 1) % segs]]
    return m


def fluted(r0, r1, z0, z1, ci, groove_ci, flutes=8, n=48):
    """Tapered round body; every n/flutes-th facet is a darker groove, like a moulded knob."""
    m = Mesh()
    nz = (r0 - r1) / (z1 - z0)
    for k in range(n):
        c = groove_ci if k % (n // flutes) == 0 else ci
        a0, a1 = 2 * math.pi * k / n, 2 * math.pi * (k + 1) / n
        v = []
        for a, r, z in ((a0, r0, z0), (a1, r0, z0), (a1, r1, z1), (a0, r1, z1)):
            l = math.sqrt(1 + nz * nz)
            v.append(m.vert((r * math.cos(a), r * math.sin(a), z), (math.cos(a) / l, math.sin(a) / l, nz / l), c))
        m.tri += [v[0], v[1], v[2], v[0], v[2], v[3]]
    return m


def knob(m, height):
    """Sifam-style mini collet knob for a 6 mm shaft: a slightly tapered black body with fine
    flutes, a rim around the top and a recess that the coloured cap presses into."""
    black, groove = colour_index(m, KNOB), colour_index(m, KNOB_GROOVE)
    rb, rt = 5.8, 5.25
    out = lathe([(rb, 0.0), (rb, 0.4)], black, 48)
    out.add(fluted(rb, rt, 0.4, height - 0.5, black, groove, flutes=16, n=48))
    out.add(lathe([(rt, height - 0.5), (rt - 0.3, height), (4.75, height), (4.75, height - 0.4), (0.0, height - 0.4)], black, 48))
    return out


def knob_cap(m, height, rgb):
    """The coloured cap: a shallow disc with a rounded edge, sitting in the knob's top recess."""
    return lathe([(4.6, height - 0.4), (4.6, height + 0.3), (4.35, height + 0.62), (0.0, height + 0.7)], colour_index(m, rgb), 40)


def encoder(m):
    """EC11 with a threaded M7 bushing and a D-shaft, as in the Alps photo (shaft axis at the origin)."""
    base, metal, shaft = colour_index(m, ENC_BASE), colour_index(m, ENC_METAL), colour_index(m, ENC_SHAFT)
    out = box((-6.2, -5.85, 1.6), (6.2, 5.85, 3.2), base)                     # insulator
    box((-5.95, -5.85, 3.2), (5.95, 5.85, 8.0), metal, out)                  # metal cover
    for sx in (-1, 1):                                                        # fold-down tabs
        box((min(sx * 5.95, sx * 6.25), -1.2, 2.2), (max(sx * 5.95, sx * 6.25), 1.2, 8.0), metal, out)
    thread, z = [(3.0, 8.0), (3.25, 8.0)], 8.0
    for _ in range(9):                                                        # M7 x 0.75-ish ridges
        thread += [(3.5, z + 0.39), (3.25, z + 0.78)]; z += 0.78
    thread += [(3.0, z)]
    out.add(lathe(thread, metal, 16))
    out.add(lathe([(3.0, z), (3.0, 25.0)], shaft, 20))
    flat = 1.5                                                                # D-flat 4.5 mm across
    a = math.asin(flat / 3.0)
    d = arc_points(0, 0, 3.0, math.pi - a, 2 * math.pi + a, 18.0)
    out.add(extrude(d, [], 25.0, SHAFT_TOP, shaft))
    seg = arc_points(0, 0, 3.0, a, math.pi - a, 18.0)                         # the step where the flat starts
    out.add(extrude(seg, [], 24.98, 25.0, shaft))
    return out


def nidec_switch(m, cap_top):
    """Nidec CFPB: black housing with a white square cap, as in its photo."""
    body, cap = colour_index(m, SWITCH_BODY), colour_index(m, SWITCH_CAP)
    out = box((-4.95, -4.95, 1.6), (4.95, 4.95, 10.4), body)
    box((-4.1, -4.1, 10.4), (4.1, 4.1, 11.0), body, out)                     # collar
    out.add(extrude(rrect(0, 0, 6.0, 6.0, 0.5), [], 11.0, cap_top - 0.35, cap))
    out.add(extrude(rrect(0, 0, 5.4, 5.4, 0.3), [], cap_top - 0.35, cap_top, cap))
    return out


def fader_cap(m, z0):
    """Narrow brushed-aluminium fader cap, 9 mm across the slot and 20 mm along it. Its top
    rises at the two ends of its length and dips to its lowest point across the middle,
    with a few fine ribs on the slopes."""
    W, L, H = 9.0, 20.0, 10.0
    yv, depth = L / 2 - 1.4, 2.2
    prof = [(-L / 2, 0.0), (L / 2, 0.0), (L / 2 - 0.3, H - 0.6), (L / 2 - 0.55, H - 0.12), (L / 2 - 0.95, H)]
    n = 16
    for k in range(n + 1):
        y = yv - 2 * yv * k / n
        rib = 0.1 if k in (2, 4, 12, 14) else 0.0
        prof.append((y, H - depth * (1 - (y / yv) ** 2) + rib))
    prof += [(-(L / 2 - 0.95), H), (-(L / 2 - 0.55), H - 0.12), (-(L / 2 - 0.3), H - 0.6)]
    e = extrude(prof, [], -W / 2, W / 2, colour_index(m, FADER_CAP))
    e.pos = [(w, u, v + z0) for u, v, w in e.pos]          # profile (y, z), run across the slot (x)
    e.nrm = [(w, u, v) for u, v, w in e.nrm]
    return e


def pem_standoff(m):
    """PEM SMTSO-M3 surface-mount standoff on the board's underside (board at z = 0, hanging down):
    a narrow pilot at the pad, a fine groove, the 5.4 mm body, a chamfered end and an M3 bore."""
    zinc, bore = colour_index(m, PEM_ZINC), colour_index(m, PEM_BORE)
    # one profile from the bore's edge out across the end face and up the outside, so every
    # face points outward or down (lathe() takes its normals from the profile's direction)
    out = lathe([(1.25, -3.0), (2.45, -3.0), (2.7, -2.75), (2.7, -0.75), (2.55, -0.75),
                 (2.55, -0.55), (2.2, -0.55), (2.2, 0.0)], zinc, 24)
    out.add(lathe([(1.25, -0.6), (1.25, -3.0)], bore, 16))          # bore wall, facing in
    out.add(lathe([(0.0, -0.6), (1.25, -0.6)], bore, 16))           # blind end, facing the opening
    return out


def stretch(m, parts, key, cut, lift):
    """Lift everything above world height cut(z values) by lift, stretching what crosses it."""
    for k in mesh_ids(parts, key):
        M = next(p['m'] for p in parts if p['mesh'] == k)
        zs = [M[8] * q[0] + M[9] * q[1] + M[10] * q[2] + M[11] for chunk in mesh_points(m, k) for q in chunk]
        c, dz = cut(zs, m, k, M), lift(zs)
        R = [M[0], M[1], M[2], M[4], M[5], M[6], M[8], M[9], M[10]]
        up = [R[6] * dz, R[7] * dz, R[8] * dz]                                 # R^T (0, 0, dz)
        def fn(q, ci, M=M, c=c, up=up):
            if M[8] * q[0] + M[9] * q[1] + M[10] * q[2] + M[11] > c:
                return [q[0] + up[0], q[1] + up[1], q[2] + up[2]]
            return q
        deform(m, k, fn)


def body_top(zs, m, k, M):
    """Top of a switch's dark body: the cap starts there."""
    dark = [M[8] * q[0] + M[9] * q[1] + M[10] * q[2] + M[11]
            for chunk, (vc, ic, P, N, I) in zip(mesh_points(m, k), m.meshes[k]['chunks'])
            for v, q in enumerate(chunk) if sum(m.palette[N[v * 4 + 3]]) < 240]
    return max(dark) + 0.02


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
    # the motor hangs under the black gearbox block at the end of the fader
    y0, y1 = sorted((s * 44.1, s * 71.5))
    mz = -16.0 - 6.0 - 0.2
    out.add(cylinder_y(0.0, mz, 6.0, y0 + 1.6, y1 - 1.6, colour_index(m, MOTOR_CAN), 24))
    out.add(cylinder_y(0.0, mz, 5.6, y0, y0 + 1.6, colour_index(m, MOTOR_END), 24))
    out.add(cylinder_y(0.0, mz, 5.6, y1 - 1.6, y1, colour_index(m, MOTOR_END), 24))
    g0, g1 = sorted((s * 52.0, s * 69.0))
    box((-4.6, g0, -16.0), (4.6, g1, -11.8), colour_index(m, GEARBOX), out)              # belt pulley housing
    return out


def fader_knob(m, cap_bottom):
    """Lever and cap above the panel (cap_bottom is relative to the mounting surface)."""
    out = box((-0.6, -4.5, -2.0), (0.6, 4.5, cap_bottom + LEVER_IN_CAP), colour_index(m, FRAME_TOP))
    out.add(fader_cap(m, cap_bottom))
    return out


def main(ctl_path, fad_path, ring_path, dxf_path, out_path):
    # the panel's height comes from the LED rings, and the switch caps and LEDs follow it
    layers = read_dxf(dxf_path)
    enc = [(STRIP_X0 + STRIP_PITCH * s + x, y - PANEL_Y0) for s in range(4) for x, y, r in layers['ENCODER']['circles']]
    assert len(enc) == 40
    ring = read_model(ring_path); ring_parts = parts_of(ring_path)
    hidden = mesh_ids(ring_parts, 'copper top', 'copper bottom', 'pads bottom', 'silkscreen bottom', 'TSSOP')
    ring_top = max(p['hi'][2] for p in ring_parts if p['mesh'] not in hidden) + RING_Z
    panel_z0 = ring_top + 0.15                # floats just above the rings
    panel_z1 = panel_z0 + 1.6
    nidec_top = panel_z1 + 3.0                # Nidec caps stick 3 mm through the panel

    asm = read_model(ctl_path); ctl_parts = parts_of(ctl_path)
    green, copper = colour_index(asm, MASK_GREEN), colour_index(asm, COPPER_GREEN)
    for k in mesh_ids(ctl_parts, '_PCB'): recolour(asm, k, to=green)
    for k in mesh_ids(ctl_parts, 'copper top', 'copper bottom'): recolour(asm, k, to=copper)
    asm.instances = [(mesh, mat, 0) for mesh, mat, *_ in asm.instances]
    cap_lift = nidec_top - max(p['hi'][2] for p in ctl_parts if 'CFPB' in p['name'])
    # TL2201 caps rise as much as the Nidec caps; the 3 mm LEDs go up on longer leads to sit flush
    stretch(asm, ctl_parts, 'TL2201', body_top, lambda zs: cap_lift)
    stretch(asm, ctl_parts, 'LED_D3', lambda zs, *_: 3.0, lambda zs: panel_z1 - max(zs))
    # the Nidec switches and the encoders are redrawn
    nidec_at = [((p['lo'][0] + p['hi'][0]) / 2, (p['lo'][1] + p['hi'][1]) / 2) for p in ctl_parts if 'CFPB' in p['name']]
    pem_at = [((p['lo'][0] + p['hi'][0]) / 2, (p['lo'][1] + p['hi'][1]) / 2) for p in ctl_parts if 'SMTSO' in p['name']]
    redrawn = mesh_ids(ctl_parts, 'CFPB', 'EC11', 'SMTSO')
    remove_instances(asm, {i for i, inst in enumerate(asm.instances) if inst[0] in redrawn})
    place(asm, nidec_switch(asm, nidec_top), [translate(x, y, 0) for x, y in nidec_at], 0)
    place(asm, pem_standoff(asm), [translate(x, y, 0) for x, y in pem_at], 0)
    place(asm, encoder(asm), [translate(x, y, 0) for x, y in enc], 0)

    # fader board, without its placeholder faders
    fad = read_model(fad_path); fad_parts = parts_of(fad_path)
    fg, fc = colour_index(fad, MASK_GREEN), colour_index(fad, COPPER_GREEN)
    for k in mesh_ids(fad_parts, '_PCB'): recolour(fad, k, to=fg)
    for k in mesh_ids(fad_parts, 'copper top', 'copper bottom'): recolour(fad, k, to=fc)
    stretch(fad, fad_parts, 'TL2201', body_top, lambda zs: cap_lift)
    stretch(fad, fad_parts, 'LED_D3', lambda zs, *_: 3.0, lambda zs: panel_z1 - max(zs))
    placeholder = mesh_ids(fad_parts, 'RSA0N11')
    add_instanced(asm, fad, [translate(FADER_BOARD[0], FADER_BOARD[1], 0)], 0, lambda i: i not in placeholder)

    # LED rings: only what shows from above (copper under black mask and the underside are skipped)
    add_instanced(asm, ring, [translate(x - RING_CENTRE[0], y - RING_CENTRE[1], RING_Z) for x, y in enc], 1,
                  lambda i: i not in hidden)

    # faceplate strips (group 2)
    plate = tint(faceplate(layers, panel_z0, panel_z1), colour_index(asm, ANODIZED))
    place(asm, plate, [translate(STRIP_X0 + STRIP_PITCH * s, -PANEL_Y0, 0) for s in range(4)], 2)

    # knob caps on the encoder shafts (group 2)
    kh = SHAFT_TOP + 0.1 - (panel_z1 + 0.6)
    place(asm, knob(asm, kh), [translate(x, y, panel_z1 + 0.6) for x, y in enc], 2)
    per_strip = len(enc) // 4                  # the DXF lists each strip's encoders top to bottom
    for rgb in sorted(set(CAP_COLOURS), key=CAP_COLOURS.index):
        spots = [translate(x, y, panel_z1 + 0.6) for i, (x, y) in enumerate(enc) if CAP_COLOURS[i % per_strip] == rgb]
        place(asm, knob_cap(asm, kh, rgb), spots, 2)

    # display glass behind each window (group 2)
    dx = [p for path in layers['DISPLAY']['paths'] for p in path]
    x0, x1 = min(p[0] for p in dx) - 0.8, max(p[0] for p in dx) + 0.8
    y0, y1 = min(p[1] for p in dx) - 0.8, max(p[1] for p in dx) + 0.8
    disp = box((x0, y0, panel_z0 - 1.35), (x1, y1, panel_z0 - 0.2), colour_index(asm, GLASS))
    box((x0 - 1.5, y0 - 3.5, panel_z0 - 3.0), (x1 + 1.5, y1 + 1.5, panel_z0 - 1.35), colour_index(asm, BEZEL), disp)
    place(asm, disp, [translate(STRIP_X0 + STRIP_PITCH * s, -PANEL_Y0, 0) for s in range(4)], 2)

    # motor faders (group 0), screwed to the panel: the frame's top plate touches the panel's
    # underside, the motor hangs through the fader board's slot at the end over its spine (-y)
    mtg = panel_z0
    fx = [STRIP_X0 + STRIP_PITCH * s + 18.15 for s in range(4)]
    fy = 86.0 - PANEL_Y0                     # slot centre (between the fader screws)
    place(asm, fader_body(asm, -1), [translate(x, fy, mtg) for x in fx], 0)
    travel = (0.62, 0.48, 0.74, 0.55)        # lever positions, 0 = bottom of the slot
    lever = fader_knob(asm, panel_z1 + 1.2 - mtg)
    place(asm, lever, [translate(x, fy + (t - 0.5) * 100.0, mtg) for x, t in zip(fx, travel)], 0)

    compact_palette(asm)
    write_model(asm, out_path)
    drawn = {}
    for mesh, mat, g in asm.instances:
        tris = sum(ic // 3 for vc, ic, *_ in asm.meshes[mesh]['chunks'])
        drawn[g] = drawn.get(g, 0) + tris
    print(f"panel {panel_z0:.2f}..{panel_z1:.2f} mm; colours {len(asm.palette)}; triangles drawn by group {drawn} "
          f"(total {sum(drawn.values())})")


if __name__ == '__main__':
    main(*sys.argv[1:6])
