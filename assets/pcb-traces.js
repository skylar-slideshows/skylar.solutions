/*
  PCB trace hover animation
  -------------------------
  Every [data-pcb] stage gets an SVG "routing" layer. When the closest
  [data-pcb-trigger] (or the stage itself) is hovered or keyboard-focused,
  purple traces route in from both board edges and land on pads at the
  edges of the image, which plays the part of chip U1. On touch screens the
  traces draw when the stage scrolls into view instead.

  The layout is plain JSON in a 1000 x 560 board. The image sits at
  x 180-820, y 40-520; keep that in sync with .pcb-chip in site.css.

  Route fields
    points  centreline, from the board edge (or a via) to the chip edge
    count   parallel traces in the bus (default 1)
    pitch   spacing between those traces (default 11)
    width   trace width (default 4)
    via     true: start on a via instead of at the board edge
    delay   ms after hover before this route starts drawing
    pulse   1: a signal pulse runs into the chip, -1: out of it
*/
(() => {
  const LAYOUT = {
    "board": { "w": 1000, "h": 560 },
    "chip": { "x": 180, "y": 40, "w": 640, "h": 480, "ref": "U1" },
    "routes": [
      { "points": [[130, 0], [130, 40], [154, 64], [180, 64]], "width": 5, "delay": 160 },
      { "points": [[0, 96], [44, 96], [92, 144], [180, 144]], "count": 4, "delay": 0, "pulse": 1 },
      { "points": [[36, 214], [64, 214], [88, 238], [180, 238]], "width": 5, "via": true, "delay": 220 },
      { "points": [[0, 330], [58, 330], [82, 306], [180, 306]], "count": 3, "delay": 80, "pulse": -1 },
      { "points": [[0, 458], [28, 458], [66, 420], [116, 420], [136, 400], [180, 400]], "count": 5, "delay": 40, "pulse": 1 },
      { "points": [[118, 560], [118, 506], [150, 474], [180, 474]], "width": 5, "delay": 300 },

      { "points": [[870, 560], [870, 520], [846, 496], [820, 496]], "width": 5, "delay": 220 },
      { "points": [[1000, 464], [956, 464], [908, 416], [820, 416]], "count": 4, "delay": 60, "pulse": 1 },
      { "points": [[964, 346], [936, 346], [912, 322], [820, 322]], "width": 5, "via": true, "delay": 280 },
      { "points": [[1000, 230], [942, 230], [918, 254], [820, 254]], "count": 3, "delay": 140, "pulse": -1 },
      { "points": [[1000, 102], [972, 102], [934, 140], [884, 140], [864, 160], [820, 160]], "count": 5, "delay": 100, "pulse": 1 },
      { "points": [[882, 0], [882, 54], [850, 86], [820, 86]], "width": 5, "delay": 360 }
    ]
  };

  const NS = "http://www.w3.org/2000/svg";
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const canHover = window.matchMedia("(hover: hover)");

  function svgEl(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const key in attrs) node.setAttribute(key, attrs[key]);
    if (parent) parent.appendChild(node);
    return node;
  }

  function unitNormal(a, b) {
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const len = Math.hypot(dx, dy);
    return [-dy / len, dx / len];
  }

  // Shift a polyline sideways by d, mitring each corner so the traces of a bus
  // stay evenly spaced through their 45-degree bends.
  function offsetLine(points, d) {
    return points.map((p, i) => {
      const before = i > 0 ? unitNormal(points[i - 1], p) : null;
      const after = i < points.length - 1 ? unitNormal(p, points[i + 1]) : null;
      if (!before || !after) {
        const n = before || after;
        return [p[0] + n[0] * d, p[1] + n[1] * d];
      }
      let mx = before[0] + after[0];
      let my = before[1] + after[1];
      const m = Math.hypot(mx, my);
      mx /= m;
      my /= m;
      const k = d / (mx * after[0] + my * after[1]);
      return [p[0] + mx * k, p[1] + my * k];
    });
  }

  function lineLength(points) {
    let total = 0;
    for (let i = 1; i < points.length; i++) {
      total += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
    }
    return total;
  }

  const fmt = (n) => Math.round(n * 10) / 10;
  const toPath = (points) => "M" + points.map((p) => fmt(p[0]) + " " + fmt(p[1])).join(" L");

  function buildLayer() {
    const { board, chip, routes } = LAYOUT;
    const svg = svgEl("svg", {
      class: "pcb-layer",
      viewBox: `0 0 ${board.w} ${board.h}`,
      "aria-hidden": "true",
      focusable: "false"
    });
    const silk = svgEl("g", { class: "pcb-silkscreen" }, svg);
    const copper = svgEl("g", { class: "pcb-copper" }, svg);
    const signals = svgEl("g", { class: "pcb-signals" }, svg);
    const lands = svgEl("g", { class: "pcb-lands" }, svg);
    const pulses = [];
    let lastArrival = 0;

    routes.forEach((route) => {
      const count = route.count || 1;
      const pitch = route.pitch || 11;
      const width = route.width || 4;

      for (let t = 0; t < count; t++) {
        const points = offsetLine(route.points, (t - (count - 1) / 2) * pitch);
        const len = lineLength(points);
        const d = toPath(points);
        const delay = (route.delay || 0) + t * 45;
        const dur = Math.round(Math.min(950, 480 + len * 1.5));
        // A dash as long as the trace, parked just before its start (round caps included).
        const hide = len + width;
        const gap = len + width * 2 + 20;

        svgEl("path", {
          class: "pcb-trace",
          d,
          "stroke-width": width,
          style: `--delay:${delay}ms;--dur:${dur}ms;--hide:${fmt(hide)}px;stroke-dasharray:${fmt(len)}px ${fmt(gap)}px`
        }, copper);

        // Landing pad where the trace meets the chip edge.
        const end = points[points.length - 1];
        const prev = points[points.length - 2];
        const horizontal = Math.abs(end[0] - prev[0]) >= Math.abs(end[1] - prev[1]);
        const long = width * 3.6;
        const short = Math.min(width * 2.2, pitch - 3);
        const pw = horizontal ? long : short;
        const ph = horizontal ? short : long;
        svgEl("rect", {
          class: "pcb-pad",
          x: fmt(end[0] - pw / 2),
          y: fmt(end[1] - ph / 2),
          width: fmt(pw),
          height: fmt(ph),
          rx: fmt(Math.min(pw, ph) * 0.3),
          style: `--delay:${Math.round(delay + dur * 0.82)}ms`
        }, lands);

        if (route.via) {
          svgEl("circle", {
            class: "pcb-via",
            cx: fmt(points[0][0]),
            cy: fmt(points[0][1]),
            r: fmt(width * 1.6),
            "stroke-width": width,
            style: `--delay:${delay}ms`
          }, lands);
        }

        if (route.pulse && t === Math.floor(count / 2)) {
          const seg = 16;
          const parked = seg + width;
          const gone = -(len + width);
          const el = svgEl("path", {
            class: "pcb-pulse",
            d,
            "stroke-width": fmt(width * 0.55),
            style: `stroke-dasharray:${seg}px ${fmt(len + seg + width * 2 + 40)}px;stroke-dashoffset:${fmt(parked)}px`
          }, signals);
          const inward = route.pulse > 0;
          pulses.push({
            el,
            from: (inward ? parked : gone) + "px",
            to: (inward ? gone : parked) + "px",
            delay: delay + dur + (pulses.length % 3) * 380,
            period: 2200 + (pulses.length % 4) * 350
          });
        }

        lastArrival = Math.max(lastArrival, delay + dur);
      }
    });

    // Silkscreen: pin-1 marker and reference designator, after the copper lands.
    const silkDelay = Math.round(lastArrival * 0.75);
    svgEl("circle", {
      class: "pcb-pin1",
      cx: chip.x - 14,
      cy: chip.y - 15,
      r: 5,
      style: `--delay:${silkDelay}ms`
    }, silk);
    const label = svgEl("text", {
      class: "pcb-silk",
      x: chip.x + 2,
      y: chip.y - 11,
      style: `--delay:${silkDelay}ms`
    }, silk);
    label.textContent = chip.ref;

    return { svg, pulses };
  }

  function wire(stage) {
    const canvas = stage.querySelector(".pcb-canvas") || stage;
    const trigger = stage.closest("[data-pcb-trigger]") || stage;
    const { svg, pulses } = buildLayer();
    canvas.appendChild(svg);

    let running = [];
    let hovered = false;
    let focused = false;

    function setLive(on) {
      if (trigger.classList.contains("is-live") === on) return;
      trigger.classList.toggle("is-live", on);
      running.forEach((a) => a.cancel());
      running = [];
      if (!on || reduceMotion.matches || !svg.animate) return;
      running = pulses.map((p) =>
        p.el.animate(
          [
            { strokeDashoffset: p.from },
            { strokeDashoffset: p.to, offset: 0.55 },
            { strokeDashoffset: p.to }
          ],
          { duration: p.period, delay: p.delay, iterations: Infinity, easing: "ease-in-out" }
        )
      );
    }

    const update = () => setLive(hovered || focused);

    if (canHover.matches) {
      trigger.addEventListener("pointerenter", () => { hovered = true; update(); });
      trigger.addEventListener("pointerleave", () => { hovered = false; update(); });
      trigger.addEventListener("focusin", () => { focused = true; update(); });
      trigger.addEventListener("focusout", (event) => {
        if (!trigger.contains(event.relatedTarget)) { focused = false; update(); }
      });
    } else if ("IntersectionObserver" in window) {
      new IntersectionObserver(
        (entries) => setLive(entries[0].isIntersecting),
        { threshold: 0.6 }
      ).observe(stage);
    }
  }

  document.querySelectorAll("[data-pcb]").forEach(wire);
})();
