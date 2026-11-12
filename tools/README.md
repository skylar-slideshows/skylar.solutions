# Rebuilding the 3D models and layer images

Browsers can't read STEP files, so each board is converted once into a small
mesh file in `assets/models/` (plus a `.bin.js` copy the viewer uses when a
page is opened straight from disk). Needs Node 18+ and Python 3 with Pillow.

`step-to-mesh.mjs` keeps polygon counts down on its own: tiny circles get as
few as 8 sides, copper/pad/silkscreen outlines are simplified, parts under 2 mm
across (0402s, 0201s) become a few coloured boxes, and pin holes under 1.3 mm
(with `--fill-holes 1.3`) are left out of the board itself, since a pin or a
tented via fills them anyway. Cards use `--tol 0.02`; the big boards `--tol 0.03`.

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

## Digital board (chip marking, for the STM32 page at /digilog/stm32/)

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
script also redraws parts from their photos (`geom.py` has the shapes): the
Nidec CFPB switches (caps 3 mm through the panel), the EC11 encoders (threaded
bushing, D-shaft), the PEM SMTSO-M3 standoffs on the underside, the knob caps (Sifam-style mini knobs at 85 % size, with coloured caps; the shafts are
cut to fit under them) and the ALPS RSA0N11M9
motor faders, screwed to the panel's underside, with narrow silver caps. The TL2201 caps are stretched up by the same
amount as the Nidec caps, and the 3 mm LEDs go up on longer leads until they
sit flush with the panel.

## Faceplate silkscreen and the interactive surface

`faceplate-silkscreen.svg` is the panel art for one strip (in points, from Illustrator).
`build-controls.py` prints it on all four panel strips of the 3D model (glyph outlines
from DejaVu Sans Condensed Bold, stretched to Arial Bold's widths), and the controls
page's top-down control surface draws it too:

    python3 tools/build-surface.py channel_strip_faceplate_v2.dxf controls.parts.json fader.parts.json assets/surface.js

The art's own <style> block sets each label's size, colour and outline, so a revised
export only needs swapping in. The 3 mm status LEDs in the model are white, with each
strip's +48 V LED red.

`surface.js` is built from `surface.template.js`. Its buttons work like the console's:
the source button lights while held and steps MIC, LINE, HI-Z; PHASE, +48V (red LED),
HPF, SOLO, MUTE (red), INS and PRE toggle; EQ, COMP and SENDS are a radio group per
strip; SEL is a radio group across all four strips (the bottom row is SOLO, MUTE, SEL
left to right, as in the firmware). The faders drag along their 100 mm travel and read
out in dB from the silkscreen's scale. Its LED rings follow the firmware
(`led_driver.c` and `knob_control.c`): 32 LEDs from D1 at 7:30 clockwise to D32 at 4:30,
left rings fill from LED 0 (0 = dark), centre rings always light LED 16 and grow either
way, and a detent is 1/32 of full scale (half with Shift, like holding the knob's switch).

## Analog assembly

The routing board with four channels of cards (20 in all), placed the way the
full assembly STEP has them. Only the assembly's board bodies are used, to
find each card's spot; the boards themselves are our own models. Because the
assembly shows 21 boards at once, they are converted lighter: coarser curves,
and ICs, sockets, headers and other boxy parts drawn as one box per coloured
piece (`--box-parts`). The fixes and thermistors are applied as usual, with
`--lite` for coarser thermistors.

    node tools/step-to-mesh.mjs bucket_routing_assembly.step asm.bin --tol 0.03 --parts asm.parts.json
    BOX="SOIC|TSSOP|SOT|MSOP|SSOP|DIP|PinHeader|PinSocket|QFN|DFN|HTSOP|SIP|Relay|Potentiometer|D_SOD|D_SMA|D_SMB|C_1206|C_1210|C_0805|R_1206|R_2010|R_0805|C_Rect|Crystal|TO-"
    node tools/step-to-mesh.mjs BUCKET_ROUTING_board.step lite/routing.bin --tol 0.06 --fill-holes 1.3 --box-small 4 --box-parts "$BOX" --parts lite/routing.json
    node tools/step-to-mesh.mjs EQ_card.step lite/eq.bin --tol 0.06 --fill-holes 1.3 --box-small 4 --box-parts "$BOX" --parts lite/eq.json
    python3 tools/fix-cards.py EQ/EQ.kicad_pcb lite/eq.bin lite/eq.json --film --bipolar -
    python3 tools/add-thermistors.py EQ/EQ.kicad_pcb lite/eq.bin lite/eq.json --lite
    (the same for input, comp, sends and output, with the options from the cards section)
    python3 tools/build-analog.py asm.parts.json lite/routing.bin lite/input.bin lite/eq.bin lite/comp.bin lite/sends.bin lite/output.bin assets/models/analog-assembly.bin

The card types are instance groups 1 to 5 (input, EQ, comp, sends, output) for
the analog page's checkboxes.

## Digital assembly

The controls assembly with the STM32 logic board and the DAC board hanging under
it, each on four 20 mm nylon hex standoffs screwed into the controls board's PEM
SMTSO-M3 standoffs (the rectangles its bottom silkscreen labels LOGIC BOARD and
DAC BOARD). Build the controls assembly first. The two boards are converted
lighter, like the analog cards, and each needs its parts list beside it as
NAME.parts.json (for the board thickness):

    BOX="$BOX|Box header|L_1206|Ferrite"
    node tools/step-to-mesh.mjs Digital_System_Dev_board.step lite/logic.bin --tol 0.06 --fill-holes 1.3 --box-small 4 --box-parts "$BOX" --parts lite/logic.parts.json
    python3 tools/add-chip-marking.py lite/logic.bin lite/logic.parts.json "LQFP-100" "STM32G474" "VET3"
    node tools/step-to-mesh.mjs CV_DAC.step lite/dac.bin --tol 0.06 --fill-holes 1.3 --box-small 4 --box-parts "$BOX" --parts lite/dac.parts.json
    python3 tools/build-digital.py assets/models/controls-assembly.bin lite/logic.bin lite/dac.bin assets/models/digital-assembly.bin

The LED rings always show in this model; the front panel (with knobs and display
glass) is instance group 2, for the digital page's one checkbox. Colours within a
small distance of one already in the model are merged, to stay inside the
viewer's 64.

## Master section boards

Both exports carry a separate "plated holes and vias" part (every barrel, about
165k triangles) that `finish-master.py` drops, since `--fill-holes` fills those
holes in the board anyway. It also gives the boards the same green as the others.
On the master analog board, `--vcas` seats a THAT2180 SIP-8 in each of the eight
1x08 sockets and turns the four axial thermistor footprints into bead thermistors,
each bent against the nearer VCA of its pair (positions come from the parts list):

    node tools/step-to-mesh.mjs MASTER_CONTROLS.step master/MASTER_CONTROLS.bin --tol 0.03 --fill-holes 1.3 --parts master/MASTER_CONTROLS.parts.json
    python3 tools/finish-master.py master/MASTER_CONTROLS.bin assets/models/master-controls.bin
    node tools/step-to-mesh.mjs MASTER_ANALOG.step master/MASTER_ANALOG.bin --tol 0.03 --fill-holes 1.3 --parts master/MASTER_ANALOG.parts.json
    python3 tools/finish-master.py master/MASTER_ANALOG.bin assets/models/master-analog.bin --vcas

## Layer images

    python3 tools/pcb-layers.py BOARD.kicad_pcb out/ PREFIX

writes one SVG per copper layer plus an all-layers SVG, and prints what each
layer carries (for the captions). Rasterize them to JPEG and WebP in
`assets/img/layers/` with any SVG renderer: cards and the controls board at
468 px wide, the fader and digital boards at 640, the LED ring at 400.

## Card schematics

The full schematics in each card page's "See schematics" box are drawn straight
from the KiCad files, one SVG per sheet, in the site's colours on light paper:

    python3 tools/render-schematic.py INPUT/INPUT.kicad_sch assets/schematics/input.svg --bipolar C3,C4,C33
    python3 tools/render-schematic.py EQ/EQ.kicad_sch assets/schematics/eq-1.svg
    python3 tools/render-schematic.py EQ/lf_hf.kicad_sch assets/schematics/eq-2.svg
    python3 tools/render-schematic.py EQ/lmf.kicad_sch assets/schematics/eq-3.svg
    python3 tools/render-schematic.py EQ/hmf.kicad_sch assets/schematics/eq-4.svg
    python3 tools/render-schematic.py COMP/COMP.kicad_sch assets/schematics/comp.svg
    python3 tools/render-schematic.py PAN_FADER/PAN_FADER.kicad_sch assets/schematics/output.svg
    python3 tools/render-schematic.py SENDS/SENDS.kicad_sch assets/schematics/sends.svg

`--check` reports how many pin ends land on a wire, junction, label or pin (a
quick test of the symbol transforms). Labels connected to nothing are left out of
the drawing (the tool lists them), so a stray label can't stretch the page. `--bipolar` lists electrolytics drawn with a
polarized symbol that are really bipolar; they're drawn without the "+".

## STEP export

Any model can go back out as a STEP file (AP242 tessellated geometry, in mm, with
colours; each part stored once and placed by an assembly structure, with the
show/hide groups as named sub-assemblies):

    python3 tools/bin-to-step.py assets/models/controls-assembly.bin digilog-controls-assembly.step "Digilog controls assembly" "1=LED rings" "2=Front panel, knobs and display glass"

After rebuilding anything, bump the `?v=` number on the pages so browsers
fetch the new files.
