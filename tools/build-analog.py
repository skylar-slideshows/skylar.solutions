#!/usr/bin/env python3
"""Build Digilog's analog section (the routing board with four channels of five cards) as one model.

    python3 tools/build-analog.py assembly.parts.json routing.bin input.bin eq.bin comp.bin sends.bin output.bin out.bin

assembly.parts.json comes from converting the full assembly STEP with step-to-mesh.mjs
--parts; only its board bodies are used, to find where each card sits. Our own card
models (with their fixes and thermistors) are then instanced at those spots, so the
assembly's rougher part models never appear. Card types are instance groups 1-5 (input,
EQ, comp, sends, output) for the page's checkboxes; with all of them off, only the
routing board is left. Every card's underside faces the routing board, about 11 mm away,
so the cards' bottom-side copper, pads and silkscreen are left out.
"""
import json, sys
from pcb3 import read_model, write_model, colour_index, mat_mul

CARDS = [("INPUT_PCB", 1), ("EQ_PCB", 2), ("COMP_PCB", 3), ("SENDS_PCB", 4), ("PAN_FADER_PCB", 5)]


def bottom_decals(m):
    """Overlay meshes (copper, pads, silkscreen) that lie on the board's underside."""
    out = set()
    for k, mesh in enumerate(m.meshes):
        top = mesh["qmin"][2] + 65535 * mesh["qstep"][2]
        if mesh["flags"] & 1 and top < 0.8: out.add(k)
    return out


def main(asm_parts, routing, *rest):
    cards, out_path = rest[:5], rest[5]
    placed = json.load(open(asm_parts))
    asm = read_model(routing)
    asm.instances = [(mesh, mat, 0) for mesh, mat, *_ in asm.instances]
    counts = {}
    for (name, group), path in zip(CARDS, cards):
        spots = [p["m"] for p in placed if p["name"] == name]
        card = read_model(path)
        skip = bottom_decals(card)
        pal = {i: colour_index(asm, c) for i, c in enumerate(card.palette)}
        remap = {}
        for k, mesh in enumerate(card.meshes):
            if k in skip: continue
            mesh = dict(mesh); chunks = []
            for vc, ic, P, N, I in mesh["chunks"]:
                n = bytearray(N)
                for v in range(vc): n[v * 4 + 3] = pal[n[v * 4 + 3]]
                chunks.append((vc, ic, P, bytes(n), I))
            mesh["chunks"] = chunks
            remap[k] = len(asm.meshes); asm.meshes.append(mesh)
        for T in spots:                       # our card's board body sits at the origin
            for mesh, mat, *_ in card.instances:
                if mesh in remap: asm.instances.append((remap[mesh], mat_mul(T, mat), group))
        counts[name] = len(spots)
    write_model(asm, out_path)
    drawn = {}
    for mesh, mat, g in asm.instances:
        drawn[g] = drawn.get(g, 0) + sum(ic // 3 for vc, ic, *_ in asm.meshes[mesh]["chunks"])
    print(f"cards placed {counts}; colours {len(asm.palette)}; triangles drawn by group {drawn} (total {sum(drawn.values())})")


if __name__ == "__main__":
    main(*sys.argv[1:])
