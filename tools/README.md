# Rebuilding the 3D models

Browsers can't read STEP files, so each board is converted once into a small
mesh file in `assets/models/` (plus a `.bin.js` copy the viewer uses when a
page is opened straight from disk). Needs Node 18+ and Python 3 with Pillow.

## Plain boards (motherboard, comp card)

    node tools/step-to-mesh.mjs BUCKET_ROUTING_board.step assets/models/bucket-routing-board.bin
    node tools/step-to-mesh.mjs COMP_card.step assets/models/comp-card.bin

## Cards with thermistors (input, EQ, sends, output)

KiCad exports a resistor model on each TH footprint. Convert with `--parts`,
then swap those for bead thermistors bent onto their VCAs:

    node tools/step-to-mesh.mjs EQ_card.step assets/models/eq-card.bin --parts eq.parts.json
    python3 tools/add-thermistors.py EQ/EQ.kicad_pcb assets/models/eq-card.bin eq.parts.json

(input-card: INPUT, sends-card: SENDS, fader-vca-card: PAN_FADER)

## Digital board (chip marking)

    node tools/step-to-mesh.mjs Digital_System_Dev_board.step assets/models/digital-dev-board.bin --parts digital.parts.json
    python3 tools/add-chip-marking.py assets/models/digital-dev-board.bin digital.parts.json "LQFP-100" "STM32G474" "VET3"

Run each patch script once per fresh conversion. After rebuilding, bump the
`?v=` number on the pages so browsers fetch the new model.
