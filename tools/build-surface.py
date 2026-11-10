#!/usr/bin/env python3
"""Build assets/surface.js: the interactive top-down control surface on the controls page.

    python3 tools/build-surface.py channel_strip_faceplate_v2.dxf controls.parts.json fader.parts.json assets/surface.js

Geometry comes from the faceplate DXF (one 36.3 mm strip, drawn four times 36.5 mm apart)
and the switch positions in the controls and fader boards' parts lists (to tell the Nidec
switches' large white caps from the TL2201s' small grey ones).

The LED rings follow the firmware (led_driver.c, knob_control.c): 32 LEDs, D1 at about
7:30 running clockwise to D32 at about 4:30; a SCALE_LEFT ring lights LEDs 0..n-1 with
n = value * 32 / 65535 (rounded), so 0 is dark; a SCALE_CENTER ring always lights LED 16
(12 o'clock) and grows left or right of it; a detent moves a value by 32767 / 32, half
that while the knob's switch is held (Shift here).
"""
import json, math, sys

STRIP_W, STRIP_PITCH, PANEL_H = 36.3, 36.5, 500.0


def read_dxf(path, step=4.0):
    lines = open(path, errors="replace").read().splitlines()
    pairs = [(lines[i].strip(), lines[i + 1].strip()) for i in range(0, len(lines) - 1, 2)]
    ents, cur = [], None
    for c, v in pairs:
        if c == "0":
            if cur: ents.append(cur)
            cur = {"type": v} if v in ("LINE", "ARC", "CIRCLE") else None
        elif cur is not None:
            cur.setdefault(c, []).append(v)
    if cur: ents.append(cur)
    f = lambda e, k: float(e[k][0])
    layers = {}
    for e in ents:
        L = layers.setdefault(e["8"][0], {"paths": [], "circles": []})
        if e["type"] == "LINE":
            L["paths"].append([(f(e, "10"), f(e, "20")), (f(e, "11"), f(e, "21"))])
        elif e["type"] == "ARC":
            a0, a1 = math.radians(f(e, "50")), math.radians(f(e, "51"))
            while a1 <= a0: a1 += 2 * math.pi
            n = max(2, int(math.ceil(math.degrees(a1 - a0) / step)))
            cx, cy, r = f(e, "10"), f(e, "20"), f(e, "40")
            L["paths"].append([(cx + r * math.cos(a0 + (a1 - a0) * k / n), cy + r * math.sin(a0 + (a1 - a0) * k / n)) for k in range(n + 1)])
        else:
            L["circles"].append((f(e, "10"), f(e, "20"), f(e, "40")))
    return layers


def chain(paths, tol=0.01):
    close = lambda a, b: abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol
    paths, loops = [list(p) for p in paths], []
    while paths:
        loop = paths.pop(0)
        while not close(loop[0], loop[-1]):
            for i, p in enumerate(paths):
                if close(loop[-1], p[0]): loop += p[1:]; break
                if close(loop[-1], p[-1]): loop += p[::-1][1:]; break
            else: raise ValueError("open outline")
            paths.pop(i)
        loops.append(loop[:-1])
    return loops


def svg_path(loop):
    """Strip-local SVG coordinates: x as drawn, y measured down from the top of the strip."""
    return "M" + "L".join(f"{x:.2f} {PANEL_H - y:.2f}" for x, y in loop) + "Z"


def main(dxf, ctl_parts_path, fad_parts_path, out):
    L = read_dxf(dxf)
    enc = [(x, PANEL_H - y) for x, y, r in L["ENCODER"]["circles"]]          # top to bottom, as in the DXF
    rings = [svg_path(l) for l in chain(L["LED_RING"]["paths"])]
    # which switch sits under each button hole (strip 0 is enough: every strip is the same)
    switches = []
    for p in json.load(open(ctl_parts_path)):
        if "CFPB" in p["name"] or "TL2201" in p["name"]:
            X, Y = (p["lo"][0] + p["hi"][0]) / 2, (p["lo"][1] + p["hi"][1]) / 2
            switches.append((X - 100.1, Y + 580.0, "big" if "CFPB" in p["name"] else "small"))
    for p in json.load(open(fad_parts_path)):
        if "TL2201" in p["name"]:
            X, Y = (p["lo"][0] + p["hi"][0]) / 2 + 70.0, (p["lo"][1] + p["hi"][1]) / 2 - 455.0
            switches.append((X - 100.1, Y + 580.0, "small"))
    buttons = []
    for loop in chain(L["BUTTON"]["paths"]):
        xs, ys = [p[0] for p in loop], [p[1] for p in loop]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        kind = min(switches, key=lambda s: math.hypot(s[0] - cx, s[1] - cy))
        buttons.append({"d": svg_path(loop), "x": round(cx, 2), "y": round(PANEL_H - cy, 2),
                        "kind": kind[2] if math.hypot(kind[0] - cx, kind[1] - cy) < 2.5 else "small"})
    display = chain(L["DISPLAY"]["paths"])[0]
    dxs, dys = [p[0] for p in display], [PANEL_H - p[1] for p in display]
    data = {
        "strip": [STRIP_W, PANEL_H], "pitch": STRIP_PITCH, "strips": 4,
        "outline": svg_path(chain(L["OUTLINE"]["paths"])[0]),
        "encoders": [[round(x, 2), round(y, 2)] for x, y in enc],
        "rings": rings,
        "buttons": buttons,
        "leds": [[round(x, 2), round(PANEL_H - y, 2)] for x, y, r in L["LED"]["circles"]],
        "display": {"d": svg_path(display), "x": round(min(dxs), 2), "y": round(min(dys), 2)},
        "fader": svg_path(chain(L["FADER"]["paths"])[0]),
        "faderX": 18.15, "faderTravel": [0.62, 0.48, 0.74, 0.55],
        "screws": [[round(x, 2), round(PANEL_H - y, 2), round(r, 2)]
                   for name in ("CHASSIS_SCREW", "FADER_SCREW", "PCB_MOUNT") for x, y, r in L[name]["circles"]],
    }
    import silkscreen
    texts, boxes = silkscreen.parse(__file__.rsplit("/", 1)[0] + "/faceplate-silkscreen.svg")
    data["silk"] = {"texts": [[round(t["x"], 3), round(t["y"], 3), round(t["size"], 3), t["colour"], t["text"]] for t in texts],
                    "boxes": [[round(b["x"], 3), round(b["y"], 3), round(b["w"], 3), round(b["h"], 3), round(b["r"], 3), b["kind"],
                               round(b["stroke"], 3)] for b in boxes]}
    js = open(__file__.rsplit("/", 1)[0] + "/surface.template.js").read().replace("/*DATA*/null", json.dumps(data, separators=(",", ":")))
    open(out, "w").write(js)
    print(f"{out}: {len(enc)} knobs, {len(buttons)} buttons, {len(data['leds'])} LEDs per strip; {len(js) // 1024} KB")


if __name__ == "__main__":
    main(*sys.argv[1:5])
