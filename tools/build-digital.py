#!/usr/bin/env python3
"""Build Digilog's digital section as one model: the controls assembly with the STM32 logic
board and the DAC board hanging underneath it.

    python3 tools/build-digital.py controls-assembly.bin logic.bin dac.bin out.bin

logic.bin and dac.bin need their step-to-mesh.mjs parts lists next to them
(NAME.parts.json), for the board thickness.

The controls board carries eight PEM SMTSO-M3 standoffs on its underside, in two 96 x 65 mm
rectangles that its bottom silkscreen labels LOGIC BOARD and DAC BOARD. A 20 mm nylon hex
standoff screws into each, and the two boards are screwed to the standoffs' ends with their
parts facing up, towards the controls board. Each board's M3 holes line up with its
rectangle with no rotation (that also puts the STM32's display connectors next to the
controls board's), so the boards only shift:

    logic board  KiCad hole (70.09, 117.0)  -> controls board (124.90, 272.70)
    DAC board    KiCad hole (105.89, 55.9)  -> controls board (125.10, 187.80)

The LED rings always show here (instance group 0); the front panel with its knobs and
display glass keeps group 2, for the page's one checkbox.
"""
import json, math, sys
from pcb3 import read_model, write_model, colour_index, mat_mul, translate, add_mesh
from geom import extrude, lathe

LOGIC_HOLES = [(124.90, 337.70), (124.90, 272.70), (220.90, 272.70), (220.90, 337.70)]
DAC_HOLES = [(125.10, 187.80), (221.10, 187.80), (125.10, 252.80), (221.10, 252.80)]
LOGIC_SHIFT = (124.90 - 70.09, 272.70 - 117.0)
DAC_SHIFT = (125.10 - 105.89, 187.80 - 55.9)
PEM_END = -3.0                 # free end of the 3 mm PEM standoffs (controls board underside at z = 0)
NYLON, NYLON_AF = 20.0, 5.5    # nylon hex standoff length and across-flats
NYLON_WHITE = (232, 229, 218)
# the DAC board was exported in KiCad's default green; it gets the same green as the other boards
DAC_COLOURS = {(9, 70, 31): (58, 89, 78), (33, 95, 46): (86, 125, 90)}


def board_top(path):
    parts = json.load(open(path.rsplit('.', 1)[0] + '.parts.json'))
    return next(p['hi'][2] for p in parts if p['name'].upper().endswith('PCB'))


def nearest_colour(dst, c, tol=14):
    """Reuse a palette colour within tol (RGB distance) instead of adding a near-duplicate:
    the merged model has to fit the viewer's 64 colours."""
    best = min(range(len(dst.palette)), key=lambda i: sum((a - b) ** 2 for a, b in zip(dst.palette[i], c)))
    if sum((a - b) ** 2 for a, b in zip(dst.palette[best], c)) <= tol * tol: return best
    return colour_index(dst, c)


def add_board(dst, src, T, recolour=None):
    pal = {i: nearest_colour(dst, (recolour or {}).get(tuple(c), tuple(c))) for i, c in enumerate(src.palette)}
    remap = {}
    for k, mesh in enumerate(src.meshes):
        mesh = dict(mesh); chunks = []
        for vc, ic, P, N, I in mesh['chunks']:
            n = bytearray(N)
            for v in range(vc): n[v * 4 + 3] = pal[n[v * 4 + 3]]
            chunks.append((vc, ic, P, bytes(n), I))
        mesh['chunks'] = chunks
        remap[k] = len(dst.meshes); dst.meshes.append(mesh)
    for mesh, mat, *_ in src.instances:
        dst.instances.append((remap[mesh], mat_mul(T, mat), 0))


def place(dst, mesh, spots):
    k = add_mesh(dst, mesh.pos, mesh.nrm, mesh.col, mesh.tri, instance=False)
    for x, y, z in spots: dst.instances.append((k, translate(x, y, z), 0))


def main(controls_path, logic_path, dac_path, out_path):
    asm = read_model(controls_path)
    # LED rings always on here; their checkbox lives on the controls board page
    asm.instances = [(mesh, mat, 0 if len(rest) == 0 or rest[0] == 1 else rest[0]) for mesh, mat, *rest in asm.instances]

    white = colour_index(asm, NYLON_WHITE)
    r = NYLON_AF / math.sqrt(3)
    hexagon = [(r * math.cos(math.radians(30 + 60 * k)), r * math.sin(math.radians(30 + 60 * k))) for k in range(6)]
    standoff = extrude(hexagon, [], PEM_END - NYLON, PEM_END, white)
    holes = LOGIC_HOLES + DAC_HOLES
    place(asm, standoff, [(x, -y, 0) for x, y in holes])

    screws = []
    for path, shift, hole_list, colours in ((logic_path, LOGIC_SHIFT, LOGIC_HOLES, None),
                                            (dac_path, DAC_SHIFT, DAC_HOLES, DAC_COLOURS)):
        top = board_top(path)
        bottom = PEM_END - NYLON - top                      # the board spans bottom .. bottom + top
        add_board(asm, read_model(path), translate(shift[0], -shift[1], bottom), colours)
        screws += [(x, -y, bottom) for x, y in hole_list]

    # nylon pan-head screws under each board (profile runs out and up, so faces point down/out)
    head = lathe([(0.0, -2.0), (2.2, -1.85), (2.75, -1.2), (2.75, 0.0)], white, 20)
    place(asm, head, screws)

    write_model(asm, out_path)
    drawn = {}
    for mesh, mat, g in asm.instances:
        drawn[g] = drawn.get(g, 0) + sum(ic // 3 for vc, ic, *_ in asm.meshes[mesh]['chunks'])
    print(f"colours {len(asm.palette)}; triangles drawn by group {drawn} (total {sum(drawn.values())})")


if __name__ == '__main__':
    main(*sys.argv[1:5])
