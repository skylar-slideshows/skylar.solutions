# Rebuilding the 3D models and layer images

Browsers can't read STEP files, so each board is converted once into a small
mesh file in `assets/models/` (plus a `.bin.js` copy the viewer uses when a
page is opened straight from disk). Needs Node 18+ and Python 3 with Pillow.

`step-to-mesh.mjs` keeps polygon counts down on its own: tiny circles get as
few as 8 sides, copper/pad/silkscreen outlines are simplified, parts under 2 mm
across (0402s, 0201s) become a few coloured boxes, and pin holes under 1.3 mm
(with `--fill-holes 1.3`) are left out of the board itself, since a pin or a
tented via fills them anyway. Cards use `--tol 0.02`; the big boards `--tol 0.03`.

## Plain boards (motherboard)

    node tools/step-to-mesh.mjs BUCKET_ROUTING_board.step assets/models/bucket-routing-board.bin --tol 0.03 --fill-holes 1.3

## Cards (input, EQ, comp, sends, output)

Convert with `--parts`, then run `fix-cards.py` (grey film caps where the
export lost them or made them red, bipolar electrolytics without their band
or silkscreen "+", a SOIC body for the output card's OPA1679), then swap the TH
resistors for bead thermistors. Keep that order: `fix-cards.py` only adds or
edits meshes, so the parts list is still valid for `add-thermistors.py`.

    node tools/step-to-mesh.mjs INPUT_card.step assets/models/input-card.bin --tol 0.02 --fill-holes 1.3 --parts input.json
    python3 tools/fix-cards.py INPUT/INPUT.kicad_pcb assets/models/input-card.bin input.json --film --bipolar C5
    python3 tools/add-thermistors.py INPUT/INPUT.kicad_pcb assets/models/input-card.bin input.json

    EQ:     fix-cards.py ... --film --bipolar -                     (then add-thermistors.py)
    output: fix-cards.py PAN_FADER/... --film --bipolar - --soic U2 (then add-thermistors.py)
    sends:  fix-cards.py ... --bipolar -                            (then add-thermistors.py)
    comp:   fix-cards.py ... --film                                 (no thermistors)

`--bipolar` takes the references to leave polarized (C5 is the input card's
big 100 µF), or `-` for none.

## Digital board (chip marking)

    node tools/step-to-mesh.mjs Digital_System_Dev_board.step assets/models/digital-dev-board.bin --tol 0.02 --fill-holes 1.3 --parts digital.json
    python3 tools/add-chip-marking.py assets/models/digital-dev-board.bin digital.json "LQFP-100" "STM32G474" "VET3"

## Controls assembly

Three boards plus the faceplate DXF become one model. The LED rings are
instance group 1 and the front panel (with knob caps and display glass) is
group 2, which the page's checkboxes switch on and off.

    node tools/step-to-mesh.mjs bucket_controls.step controls.bin --tol 0.03 --fill-holes 1.3 --parts controls.parts.json
    node tools/step-to-mesh.mjs bucket_fader.step fader.bin --tol 0.03 --fill-holes 1.3 --parts fader.parts.json
    node tools/step-to-mesh.mjs led_ring.step ring.bin --tol 0.03 --fill-holes 1.3 --parts ring.parts.json
    python3 tools/build-controls.py controls.bin fader.bin ring.bin channel_strip_faceplate_v2.dxf assets/models/controls-assembly.bin

Positions come from the faceplate: panel (x, y) sits over controls-board KiCad
(100.1 + x, 580 − y), strips 36.5 mm apart, and the fader board is offset
(+70, +455) from the controls board (found from its M2 mounting holes). The
faders are drawn from the ALPS RSA0N11M9 drawing (`geom.py` has the shapes).

## Layer images

    python3 tools/pcb-layers.py BOARD.kicad_pcb out/ PREFIX

writes one SVG per copper layer plus an all-layers SVG, and prints what each
layer carries (for the captions). Rasterize them to JPEG and WebP in
`assets/img/layers/` with any SVG renderer: cards and the controls board at
468 px wide, the fader and digital boards at 640, the LED ring at 400.

After rebuilding anything, bump the `?v=` number on the pages so browsers
fetch the new files.
