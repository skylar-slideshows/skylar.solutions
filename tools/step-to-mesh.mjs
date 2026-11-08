#!/usr/bin/env node
/*
  step-to-mesh.mjs
  ----------------
  Converts a STEP board model (AP203/AP214, e.g. a KiCad export) into the small
  binary mesh that the 3D viewer on /digilog loads. Browsers can't read STEP,
  so this does the CAD work ahead of time: it walks the assembly, keeps the
  face/solid colours, tessellates every face, and writes quantized vertex
  data split into 16-bit-index chunks (renders on any WebGL 1 device).

  Usage
    node tools/step-to-mesh.mjs <input.step> <output.bin> [--tol 0.01] [--mask-shade 0.72]

  --tol         chordal tolerance in model units (mm). Smaller = smoother, bigger file.
  --mask-shade  darkens the solder mask green (default 0.72; 1 = colours as exported).
  --parts FILE  also write a JSON list of placed parts (for the add-* patch scripts).

  Also writes <output.bin>.js, a script copy of the same model that the viewer
  uses when a page is opened from disk (file://) instead of a web server.

  Supported geometry: planes, cylinders, cones, spheres, tori, surfaces of revolution and
  linear extrusion, and
  (rational, open or closed) B-spline surfaces, bounded by lines, circles,
  ellipses and (rational) B-spline curves. That covers typical KiCad /
  OpenCascade exports. Example (from the site root):
    node tools/step-to-mesh.mjs board.step assets/models/bucket-routing-board.bin

  Output format (little-endian, every array padded to 4 bytes):
    "PCB3", u32 version=1
    u32 colourCount, colourCount x (u8 r, g, b, pad)
    f32 worldMin[3], f32 worldMax[3]
    u32 meshCount
      per mesh: u32 flags (bit 0 = overlay layer such as copper/pads/silkscreen,
                bits 1-3 = its stacking order), f32 qMin[3], f32 qStep[3], u32 chunkCount
        per chunk: u32 vertexCount, u32 indexCount,
                   u16 position[vertexCount*3]        (p = qMin + q * qStep)
                   i8  normalColour[vertexCount*4]    (normal xyz * 127, colour index)
                   u16 index[indexCount]
    u32 instanceCount, per instance: u32 meshIndex, f32 matrix[12] (3x4, row-major)

  The triangulation follows the approach of Mapbox's earcut (ISC licence).
*/
import { readFileSync, writeFileSync } from "node:fs";

/* ------------------------------------------------------------------------ */
/* Options                                                                  */
/* ------------------------------------------------------------------------ */
const argv = process.argv.slice(2);
if (argv.length < 2) {
  console.error("usage: node step-to-mesh.mjs <input.step> <output.bin> [--tol 0.01]");
  process.exit(1);
}
const INPUT = argv[0];
const OUTPUT = argv[1];
const tolFlag = argv.indexOf("--tol");
const TOL = tolFlag >= 0 ? parseFloat(argv[tolFlag + 1]) : 0.01;
const shadeFlag = argv.indexOf("--mask-shade");
// KiCad exports a bright solder mask green, and the copper under the mask is
// brighter still, so boards read as lime. Green-dominant colours (the mask
// and the copper under it) are scaled by this. --mask-shade 1 turns it off.
const MASK_SHADE = shadeFlag >= 0 ? parseFloat(argv[shadeFlag + 1]) : 0.72;
const partsFlag = argv.indexOf("--parts");
// --parts list.json: also write every placed part (name, mesh, matrix, world
// bounds). tools/add-thermistors.py and tools/add-chip-marking.py use it.
const PARTS_OUT = partsFlag >= 0 ? argv[partsFlag + 1] : null;
const MIN_STEP = Math.PI / 12; // finest angular step for arcs (15 deg)
const MAX_STEP = Math.PI / 6;  // coarsest angular step for arcs (30 deg)
const MAX_NORMAL_TURN = Math.cos(Math.PI / 6); // refine curved faces until normals turn < 30 deg per edge
const REFINE_TOL = () => 3 * TOL;               // curved faces: chord error allowed inside a face (matches 15 deg arcs)
const DEFAULT_COLOUR = [0.72, 0.72, 0.74];
const TWO_PI = Math.PI * 2;

/* ------------------------------------------------------------------------ */
/* STEP parser                                                              */
/* ------------------------------------------------------------------------ */
class Ref {
  constructor(id) { this.id = id; }
}

function parseStep(text) {
  const start = text.indexOf("DATA;");
  const end = text.indexOf("ENDSEC;", start);
  const s = text.slice(start + 5, end);
  const ents = new Map();
  const n = s.length;
  let p = 0;
  while (p < n) {
    const h = s.indexOf("#", p);
    if (h < 0) break;
    let q = h + 1;
    while (q < n && s.charCodeAt(q) >= 48 && s.charCodeAt(q) <= 57) q++;
    const id = Number(s.slice(h + 1, q));
    while (q < n && s.charCodeAt(q) <= 32) q++;
    if (s[q] !== "=") { p = q; continue; }
    q++;
    let r = q;
    let inString = false;
    for (; r < n; r++) {
      const c = s.charCodeAt(r);
      if (c === 39) inString = !inString;
      else if (c === 59 && !inString) break;
    }
    ents.set(id, parseRecord(s.slice(q, r)));
    p = r + 1;
  }
  return ents;
}

function parseRecord(src) {
  const st = { s: src, p: 0 };
  skipWs(st);
  if (src[st.p] === "(") {
    st.p++;
    const parts = {};
    for (;;) {
      skipWs(st);
      if (st.p >= src.length || src[st.p] === ")") break;
      const name = readName(st);
      skipWs(st);
      parts[name] = readValue(st);
    }
    return { type: "", parts };
  }
  const type = readName(st);
  skipWs(st);
  return { type, args: readValue(st) };
}

function skipWs(st) {
  const s = st.s;
  while (st.p < s.length && s.charCodeAt(st.p) <= 32) st.p++;
}

function readName(st) {
  const s = st.s;
  const b = st.p;
  while (st.p < s.length) {
    const c = s.charCodeAt(st.p);
    if ((c >= 65 && c <= 90) || (c >= 97 && c <= 122) || (c >= 48 && c <= 57) || c === 95 || c === 45) st.p++;
    else break;
  }
  return s.slice(b, st.p);
}

function readValue(st) {
  skipWs(st);
  const s = st.s;
  const c = s[st.p];
  if (c === "(") {
    st.p++;
    const list = [];
    skipWs(st);
    if (s[st.p] === ")") { st.p++; return list; }
    for (;;) {
      list.push(readValue(st));
      skipWs(st);
      if (s[st.p] === ",") { st.p++; continue; }
      if (s[st.p] === ")") { st.p++; break; }
      throw new Error("Unexpected character in list: " + s.slice(st.p, st.p + 40));
    }
    return list;
  }
  if (c === "'") {
    let out = "";
    let q = st.p + 1;
    for (;;) {
      const e = s.indexOf("'", q);
      if (e < 0) { st.p = s.length; return out + s.slice(q); }
      if (s[e + 1] === "'") { out += s.slice(q, e + 1); q = e + 2; }
      else { out += s.slice(q, e); st.p = e + 1; return out; }
    }
  }
  if (c === "#") {
    st.p++;
    const b = st.p;
    while (st.p < s.length && s.charCodeAt(st.p) >= 48 && s.charCodeAt(st.p) <= 57) st.p++;
    return new Ref(Number(s.slice(b, st.p)));
  }
  if (c === ".") {
    const e = s.indexOf(".", st.p + 1);
    const v = s.slice(st.p, e + 1);
    st.p = e + 1;
    return v;
  }
  if (c === "$" || c === "*") { st.p++; return null; }
  const code = s.charCodeAt(st.p);
  if (c === "-" || c === "+" || (code >= 48 && code <= 57)) {
    const b = st.p;
    st.p++;
    while (st.p < s.length) {
      const d = s.charCodeAt(st.p);
      if ((d >= 48 && d <= 57) || d === 46 || d === 69 || d === 101 || d === 43 || d === 45) st.p++;
      else break;
    }
    return parseFloat(s.slice(b, st.p));
  }
  const name = readName(st);
  skipWs(st);
  if (s[st.p] === "(") {
    st.p++;
    const inner = readValue(st);
    skipWs(st);
    if (s[st.p] === ")") st.p++;
    return { typed: name, value: inner };
  }
  return name;
}

/* ------------------------------------------------------------------------ */
/* Small vector / matrix helpers                                            */
/* ------------------------------------------------------------------------ */
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scl = (a, k) => [a[0] * k, a[1] * k, a[2] * k];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a) => Math.sqrt(dot(a, a));
const dist = (a, b) => len(sub(a, b));
const norm = (a) => { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];

// 3x4 row-major rigid transforms
const IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0];
function matMul(a, b) {
  const o = new Array(12);
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 4; c++) {
      o[r * 4 + c] = a[r * 4] * b[c] + a[r * 4 + 1] * b[4 + c] + a[r * 4 + 2] * b[8 + c] + (c === 3 ? a[r * 4 + 3] : 0);
    }
  }
  return o;
}
function matInvRigid(m) {
  const r = [m[0], m[4], m[8], 0, m[1], m[5], m[9], 0, m[2], m[6], m[10], 0];
  const t = [m[3], m[7], m[11]];
  for (let i = 0; i < 3; i++) r[i * 4 + 3] = -(r[i * 4] * t[0] + r[i * 4 + 1] * t[1] + r[i * 4 + 2] * t[2]);
  return r;
}
const matPoint = (m, p) => [
  m[0] * p[0] + m[1] * p[1] + m[2] * p[2] + m[3],
  m[4] * p[0] + m[5] * p[1] + m[6] * p[2] + m[7],
  m[8] * p[0] + m[9] * p[1] + m[10] * p[2] + m[11],
];

/* ------------------------------------------------------------------------ */
/* Load                                                                     */
/* ------------------------------------------------------------------------ */
const t0 = Date.now();
const E = parseStep(readFileSync(INPUT, "latin1"));
console.log(`parsed ${E.size} entities in ${((Date.now() - t0) / 1000).toFixed(1)}s`);

const ent = (id) => {
  const e = E.get(id);
  if (!e) throw new Error("missing entity #" + id);
  return e;
};
const args = (id) => ent(id).args;
const typeOf = (id) => ent(id).type;
const warnings = new Map();
function warn(msg) { warnings.set(msg, (warnings.get(msg) || 0) + 1); }

function pointOf(id) {
  const c = args(id)[1];
  return [c[0], c[1], c.length > 2 ? c[2] : 0];
}
function dirOf(id) {
  const c = args(id)[1];
  return norm([c[0], c[1], c.length > 2 ? c[2] : 0]);
}
function anyPerp(z) {
  const t = Math.abs(z[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
  return norm(sub(t, scl(z, dot(t, z))));
}
const frameCache = new Map();
function frameOf(id) {
  let f = frameCache.get(id);
  if (f) return f;
  const a = args(id); // AXIS2_PLACEMENT_3D(name, location, axis, ref_direction)
  const o = pointOf(a[1].id);
  const z = a[2] ? dirOf(a[2].id) : [0, 0, 1];
  let x = a[3] ? dirOf(a[3].id) : anyPerp(z);
  x = norm(sub(x, scl(z, dot(x, z))));
  f = { o, x, y: cross(z, x), z };
  frameCache.set(id, f);
  return f;
}
function placementMatrix(id) {
  if (typeOf(id) !== "AXIS2_PLACEMENT_3D") return IDENTITY;
  const f = frameOf(id);
  return [f.x[0], f.y[0], f.z[0], f.o[0], f.x[1], f.y[1], f.z[1], f.o[1], f.x[2], f.y[2], f.z[2], f.o[2]];
}

/* ------------------------------------------------------------------------ */
/* Curves                                                                   */
/* ------------------------------------------------------------------------ */
function arcStep(r) {
  const s = r > TOL ? 2 * Math.acos(1 - TOL / r) : MAX_STEP;
  return Math.min(MAX_STEP, Math.max(MIN_STEP, s));
}

// Expanded knot vector from multiplicities
function expandKnots(mults, knots) {
  const out = [];
  for (let i = 0; i < knots.length; i++) for (let k = 0; k < mults[i]; k++) out.push(knots[i]);
  return out;
}
function findSpan(n, p, u, U) {
  if (u >= U[n + 1]) return n;
  if (u <= U[p]) return p;
  let lo = p;
  let hi = n + 1;
  let mid = (lo + hi) >> 1;
  while (u < U[mid] || u >= U[mid + 1]) {
    if (u < U[mid]) hi = mid; else lo = mid;
    mid = (lo + hi) >> 1;
  }
  return mid;
}
function basisFuns(i, u, p, U) {
  const N = [1];
  const left = [];
  const right = [];
  for (let j = 1; j <= p; j++) {
    left[j] = u - U[i + 1 - j];
    right[j] = U[i + j] - u;
    let saved = 0;
    for (let r = 0; r < j; r++) {
      const den = right[r + 1] + left[j - r];
      const temp = den !== 0 ? N[r] / den : 0;
      N[r] = saved + right[r + 1] * temp;
      saved = left[j - r] * temp;
    }
    N[j] = saved;
  }
  return N;
}

const curveCache = new Map();
function bsplineCurve(id) {
  let c = curveCache.get(id);
  if (c) return c;
  const e = ent(id);
  let deg, ctrlRefs, mults, knots, weights = null;
  if (e.type === "B_SPLINE_CURVE_WITH_KNOTS") {
    const a = e.args;
    deg = a[1]; ctrlRefs = a[2]; mults = a[6]; knots = a[7];
  } else if (e.type === "") {
    const bs = e.parts.B_SPLINE_CURVE;
    const bk = e.parts.B_SPLINE_CURVE_WITH_KNOTS;
    deg = bs[0]; ctrlRefs = bs[1]; mults = bk[0]; knots = bk[1];
    if (e.parts.RATIONAL_B_SPLINE_CURVE) weights = e.parts.RATIONAL_B_SPLINE_CURVE[0];
  } else {
    throw new Error("not a b-spline curve: " + e.type);
  }
  const ctrl = ctrlRefs.map((r) => pointOf(r.id));
  const U = expandKnots(mults, knots);
  c = { deg, ctrl, w: weights, U, n: ctrl.length - 1, tmin: U[deg], tmax: U[ctrl.length] };
  curveCache.set(id, c);
  return c;
}
function evalBspline(c, t) {
  const span = findSpan(c.n, c.deg, t, c.U);
  const N = basisFuns(span, t, c.deg, c.U);
  let x = 0, y = 0, z = 0, w = 0;
  for (let j = 0; j <= c.deg; j++) {
    const k = span - c.deg + j;
    const wk = c.w ? c.w[k] : 1;
    const b = N[j] * wk;
    x += b * c.ctrl[k][0]; y += b * c.ctrl[k][1]; z += b * c.ctrl[k][2]; w += b;
  }
  return [x / w, y / w, z / w];
}
// Parameter of the curve point closest to p
function bsplineParamOf(c, p) {
  if (dist(evalBspline(c, c.tmin), p) < 1e-6) return c.tmin;
  if (dist(evalBspline(c, c.tmax), p) < 1e-6) return c.tmax;
  const N = 64 * Math.max(1, c.n);
  let best = c.tmin, bestD = Infinity;
  for (let i = 0; i <= N; i++) {
    const t = c.tmin + (c.tmax - c.tmin) * (i / N);
    const d = dist(evalBspline(c, t), p);
    if (d < bestD) { bestD = d; best = t; }
  }
  let h = (c.tmax - c.tmin) / N;
  for (let it = 0; it < 40; it++) {
    const a = Math.max(c.tmin, best - h), b = Math.min(c.tmax, best + h);
    const da = dist(evalBspline(c, a), p), db = dist(evalBspline(c, b), p);
    if (da < bestD) { bestD = da; best = a; } else if (db < bestD) { bestD = db; best = b; } else h *= 0.5;
  }
  return best;
}

// Adaptive sampling of any parametric curve between ta and tb
function sampleCurve(fn, ta, tb, minSegs) {
  const pts = [];
  const segs = Math.max(1, minSegs);
  let prevT = ta;
  let prevP = fn(ta);
  pts.push(prevP);
  for (let i = 1; i <= segs; i++) {
    const t = ta + (tb - ta) * (i / segs);
    const pt = fn(t);
    refineSegment(fn, prevT, t, prevP, pt, 0, pts);
    pts.push(pt);
    prevT = t; prevP = pt;
  }
  return pts;
}
function refineSegment(fn, ta, tb, pa, pb, depth, out) {
  if (depth > 6) return;
  const tm = (ta + tb) / 2;
  const pm = fn(tm);
  if (dist(pm, mix(pa, pb, 0.5)) > TOL) {
    refineSegment(fn, ta, tm, pa, pm, depth + 1, out);
    out.push(pm);
    refineSegment(fn, tm, tb, pm, pb, depth + 1, out);
  }
}

function underlyingCurve(id) {
  // SURFACE_CURVE / SEAM_CURVE / BOUNDED_SURFACE_CURVE(name, curve_3d, ...)
  let cur = id;
  for (let guard = 0; guard < 4; guard++) {
    const t = typeOf(cur);
    if (t === "SURFACE_CURVE" || t === "SEAM_CURVE" || t === "BOUNDED_SURFACE_CURVE") cur = args(cur)[1].id;
    else if (t === "TRIMMED_CURVE") cur = args(cur)[1].id;
    else break;
  }
  return cur;
}
function isBsplineCurve(id) {
  const e = ent(id);
  return e.type === "B_SPLINE_CURVE_WITH_KNOTS" || (e.type === "" && !!e.parts.B_SPLINE_CURVE);
}

const vertexCache = new Map();
function vertexPoint(id) {
  let p = vertexCache.get(id);
  if (!p) { p = pointOf(args(id)[1].id); vertexCache.set(id, p); }
  return p;
}

// Points along an EDGE_CURVE, from its start vertex to its end vertex
const edgeCache = new Map();
function edgePoints(id) {
  let pts = edgeCache.get(id);
  if (pts) return pts;
  const a = args(id); // EDGE_CURVE(name, start, end, curve, same_sense)
  const p0 = vertexPoint(a[1].id);
  const p1 = vertexPoint(a[2].id);
  const closed = a[1].id === a[2].id || dist(p0, p1) < 1e-9;
  const sense = a[4] === ".T.";
  const cid = underlyingCurve(a[3].id);
  const t = typeOf(cid);
  if (t === "LINE") {
    pts = [p0, p1];
  } else if (t === "CIRCLE" || t === "ELLIPSE") {
    const ca = args(cid);
    const f = frameOf(ca[1].id);
    const rx = ca[2];
    const ry = t === "ELLIPSE" ? ca[3] : ca[2];
    const ang = (p) => { const d = sub(p, f.o); return Math.atan2(dot(d, f.y) / ry, dot(d, f.x) / rx); };
    const a0 = ang(p0);
    let sweep;
    if (closed) sweep = sense ? TWO_PI : -TWO_PI;
    else {
      sweep = ang(p1) - a0;
      if (sense) { while (sweep <= 1e-12) sweep += TWO_PI; while (sweep > TWO_PI) sweep -= TWO_PI; }
      else { while (sweep >= -1e-12) sweep -= TWO_PI; while (sweep < -TWO_PI) sweep += TWO_PI; }
    }
    const steps = Math.max(1, Math.ceil(Math.abs(sweep) / arcStep(Math.max(rx, ry)) - 1e-9));
    pts = [];
    for (let i = 0; i <= steps; i++) {
      const th = a0 + (sweep * i) / steps;
      pts.push(add(f.o, add(scl(f.x, rx * Math.cos(th)), scl(f.y, ry * Math.sin(th)))));
    }
  } else if (isBsplineCurve(cid)) {
    const c = bsplineCurve(cid);
    let ta, tb;
    if (closed) { ta = sense ? c.tmin : c.tmax; tb = sense ? c.tmax : c.tmin; }
    else { ta = bsplineParamOf(c, p0); tb = bsplineParamOf(c, p1); }
    const spans = Math.max(2, Math.round((c.n - c.deg + 1) * Math.abs(tb - ta) / (c.tmax - c.tmin)) * 2);
    pts = sampleCurve((tt) => evalBspline(c, tt), ta, tb, Math.min(64, spans));
  } else {
    warn("unsupported curve " + t + " (drawn as a straight line)");
    pts = [p0, p1];
  }
  pts[0] = p0;
  pts[pts.length - 1] = p1;
  edgeCache.set(id, pts);
  return pts;
}

/* ------------------------------------------------------------------------ */
/* Surfaces: param(P) -> [u,v], point(u,v), normal(u,v)                     */
/* ------------------------------------------------------------------------ */
function surfaceOf(id) {
  const e = ent(id);
  const t = e.type;
  if (t === "PLANE") {
    const f = frameOf(e.args[1].id);
    return {
      kind: "plane", planar: true,
      param: (p) => { const d = sub(p, f.o); return [dot(d, f.x), dot(d, f.y)]; },
      point: (u, v) => add(f.o, add(scl(f.x, u), scl(f.y, v))),
      normal: () => f.z,
    };
  }
  if (t === "CYLINDRICAL_SURFACE") {
    const f = frameOf(e.args[1].id);
    const r = e.args[2];
    return {
      kind: "cylinder", periodicU: true, angularU: true, scaleU: r, scaleV: 1,
      param: (p) => { const d = sub(p, f.o); return [Math.atan2(dot(d, f.y), dot(d, f.x)), dot(d, f.z)]; },
      point: (u, v) => add(f.o, add(add(scl(f.x, r * Math.cos(u)), scl(f.y, r * Math.sin(u))), scl(f.z, v))),
      normal: (u) => add(scl(f.x, Math.cos(u)), scl(f.y, Math.sin(u))),
    };
  }
  if (t === "TOROIDAL_SURFACE") {
    const f = frameOf(e.args[1].id);
    const R = e.args[2];
    const r = e.args[3];
    return {
      kind: "torus", periodicU: true, periodicV: true, angularU: true, angularV: true, scaleU: R, scaleV: r,
      param: (p) => {
        const d = sub(p, f.o);
        const px = dot(d, f.x), py = dot(d, f.y), pz = dot(d, f.z);
        return [Math.atan2(py, px), Math.atan2(pz, Math.sqrt(px * px + py * py) - R)];
      },
      point: (u, v) => {
        const rad = R + r * Math.cos(v);
        return add(f.o, add(add(scl(f.x, rad * Math.cos(u)), scl(f.y, rad * Math.sin(u))), scl(f.z, r * Math.sin(v))));
      },
      normal: (u, v) => add(add(scl(f.x, Math.cos(v) * Math.cos(u)), scl(f.y, Math.cos(v) * Math.sin(u))), scl(f.z, Math.sin(v))),
    };
  }
  if (t === "CONICAL_SURFACE") {
    const f = frameOf(e.args[1].id);
    const r = e.args[2];
    const ang = e.args[3] > Math.PI / 2 ? (e.args[3] * Math.PI) / 180 : e.args[3]; // radians (degrees if clearly so)
    const ta = Math.tan(ang), ca = Math.cos(ang), sa = Math.sin(ang);
    return {
      kind: "cone", periodicU: true, angularU: true, scaleU: Math.max(r, 1e-3), scaleV: 1,
      param: (p) => { const d = sub(p, f.o); return [Math.atan2(dot(d, f.y), dot(d, f.x)), dot(d, f.z)]; },
      point: (u, v) => {
        const rad = r + v * ta;
        return add(f.o, add(add(scl(f.x, rad * Math.cos(u)), scl(f.y, rad * Math.sin(u))), scl(f.z, v)));
      },
      normal: (u) => add(scl(add(scl(f.x, Math.cos(u)), scl(f.y, Math.sin(u))), ca), scl(f.z, -sa)),
      isPole: (p) => Math.abs(r + dot(sub(p, f.o), f.z) * ta) < 1e-7, // the apex
    };
  }
  if (t === "SPHERICAL_SURFACE") {
    const f = frameOf(e.args[1].id);
    const r = e.args[2];
    const dirAt = (u, v) => add(add(scl(f.x, Math.cos(v) * Math.cos(u)), scl(f.y, Math.cos(v) * Math.sin(u))), scl(f.z, Math.sin(v)));
    return {
      kind: "sphere", periodicU: true, angularU: true, angularV: true, scaleU: r, scaleV: r,
      param: (p) => {
        const d = sub(p, f.o);
        const x = dot(d, f.x), y = dot(d, f.y), z = dot(d, f.z);
        return [Math.atan2(y, x), Math.atan2(z, Math.sqrt(x * x + y * y))];
      },
      point: (u, v) => add(f.o, scl(dirAt(u, v), r)),
      normal: (u, v) => dirAt(u, v),
      isPole: (p) => { const d = sub(p, f.o); const x = dot(d, f.x), y = dot(d, f.y); return Math.sqrt(x * x + y * y) < 1e-7 * Math.max(1, r); },
    };
  }
  if (t === "SURFACE_OF_REVOLUTION") return revolutionSurface(e);
  if (t === "SURFACE_OF_LINEAR_EXTRUSION") return extrusionSurface(e);
  if (t === "B_SPLINE_SURFACE_WITH_KNOTS" || (t === "" && e.parts.B_SPLINE_SURFACE)) return bsplineSurface(e);
  return null;
}

function curveFunction(id) {
  const cid = underlyingCurve(id);
  const t = typeOf(cid);
  if (isBsplineCurve(cid)) {
    const c = bsplineCurve(cid);
    return { fn: (s) => evalBspline(c, s), tmin: c.tmin, tmax: c.tmax };
  }
  if (t === "LINE") {
    const a = args(cid);
    const p = pointOf(a[1].id);
    const va = args(a[2].id); // VECTOR(name, direction, magnitude)
    const d = scl(dirOf(va[1].id), va[2]);
    return { fn: (s) => add(p, scl(d, s)), tmin: -1e3, tmax: 1e3 };
  }
  if (t === "CIRCLE") {
    const a = args(cid);
    const f = frameOf(a[1].id);
    const r = a[2];
    return { fn: (s) => add(f.o, add(scl(f.x, r * Math.cos(s)), scl(f.y, r * Math.sin(s)))), tmin: 0, tmax: TWO_PI };
  }
  return null;
}

function revolutionSurface(e) {
  const prof = curveFunction(e.args[1].id);
  const ax = args(e.args[2].id); // AXIS1_PLACEMENT(name, location, axis)
  const o = pointOf(ax[1].id);
  const a = ax[2] ? dirOf(ax[2].id) : [0, 0, 1];
  if (!prof) return null;
  // the profile's own radial direction defines u = 0
  const mid = prof.fn((Math.max(prof.tmin, -1e2) + Math.min(prof.tmax, 1e2)) / 2);
  let xr = sub(sub(mid, o), scl(a, dot(sub(mid, o), a)));
  xr = len(xr) > 1e-9 ? norm(xr) : anyPerp(a);
  const yr = cross(a, xr);
  const rh = (p) => { const d = sub(p, o); const h = dot(d, a); return [len(sub(d, scl(a, h))), h]; };
  // sample the profile in (radius, height) for parameter inversion
  const samples = [];
  const lo = Math.max(prof.tmin, -1e2), hi = Math.min(prof.tmax, 1e2);
  for (let i = 0; i <= 256; i++) { const s = lo + ((hi - lo) * i) / 256; samples.push([s, rh(prof.fn(s))]); }
  const point = (u, v) => {
    const [r, h] = rh(prof.fn(v));
    return add(o, add(scl(a, h), add(scl(xr, r * Math.cos(u)), scl(yr, r * Math.sin(u)))));
  };
  return {
    kind: "revolution", periodicU: true, angularU: true, scaleU: 1, scaleV: 1, point,
    param: (p) => {
      const d = sub(p, o);
      const u = Math.atan2(dot(d, yr), dot(d, xr));
      const q = rh(p);
      let best = 0, bestD = Infinity;
      for (let i = 0; i < samples.length; i++) {
        const dd = Math.hypot(samples[i][1][0] - q[0], samples[i][1][1] - q[1]);
        if (dd < bestD) { bestD = dd; best = i; }
      }
      let s = samples[best][0];
      let h = (hi - lo) / 256;
      for (let it = 0; it < 40; it++) {
        const c1 = rh(prof.fn(Math.max(lo, s - h))), c2 = rh(prof.fn(Math.min(hi, s + h)));
        const d1 = Math.hypot(c1[0] - q[0], c1[1] - q[1]), d2 = Math.hypot(c2[0] - q[0], c2[1] - q[1]);
        if (d1 < bestD) { bestD = d1; s = Math.max(lo, s - h); } else if (d2 < bestD) { bestD = d2; s = Math.min(hi, s + h); } else h *= 0.5;
      }
      return [u, s];
    },
    normal: (u, v) => numericNormal(point, u, v, 1e-4, 1e-4 * (hi - lo)),
  };
}

// A curve swept along a straight line: S(u, v) = C(u) + v * V.
function extrusionSurface(e) {
  const prof = curveFunction(e.args[1].id);
  if (!prof) return null;
  const va = args(e.args[2].id); // VECTOR(name, direction, magnitude)
  const dir = dirOf(va[1].id);
  const mag = va[2] || 1;
  const lo = Math.max(prof.tmin, -1e2), hi = Math.min(prof.tmax, 1e2);
  // find u by looking along the sweep direction (it drops out of the distance)
  const flat = (p) => sub(p, scl(dir, dot(p, dir)));
  const samples = [];
  let arc = 0;
  for (let i = 0; i <= 256; i++) {
    const s = lo + ((hi - lo) * i) / 256;
    const q = flat(prof.fn(s));
    if (i) arc += dist(q, samples[i - 1][1]);
    samples.push([s, q]);
  }
  const point = (u, v) => add(prof.fn(u), scl(dir, v * mag));
  return {
    kind: "extrusion", point,
    scaleU: arc / (hi - lo || 1), scaleV: mag,
    param: (p) => {
      const q = flat(p);
      let best = 0, bestD = Infinity;
      for (let i = 0; i < samples.length; i++) { const dd = dist(samples[i][1], q); if (dd < bestD) { bestD = dd; best = i; } }
      let u = samples[best][0];
      let h = (hi - lo) / 256;
      for (let it = 0; it < 40; it++) {
        const a1 = Math.max(lo, u - h), a2 = Math.min(hi, u + h);
        const d1 = dist(flat(prof.fn(a1)), q), d2 = dist(flat(prof.fn(a2)), q);
        if (d1 < bestD) { bestD = d1; u = a1; } else if (d2 < bestD) { bestD = d2; u = a2; } else h *= 0.5;
      }
      return [u, dot(sub(p, prof.fn(u)), dir) / mag];
    },
    normal: (u, v) => numericNormal(point, u, v, 1e-4 * (hi - lo), 1e-4),
  };
}

function bsplineSurface(e) {
  let du, dv, ctrlRefs, um, vm, uk, vk, W = null;
  if (e.type === "B_SPLINE_SURFACE_WITH_KNOTS") {
    const a = e.args;
    du = a[1]; dv = a[2]; ctrlRefs = a[3]; um = a[8]; vm = a[9]; uk = a[10]; vk = a[11];
  } else {
    const bs = e.parts.B_SPLINE_SURFACE;
    const bk = e.parts.B_SPLINE_SURFACE_WITH_KNOTS;
    du = bs[0]; dv = bs[1]; ctrlRefs = bs[2]; um = bk[0]; vm = bk[1]; uk = bk[2]; vk = bk[3];
    if (e.parts.RATIONAL_B_SPLINE_SURFACE) W = e.parts.RATIONAL_B_SPLINE_SURFACE[0];
  }
  const P = ctrlRefs.map((row) => row.map((r) => pointOf(r.id)));
  const U = expandKnots(um, uk);
  const V = expandKnots(vm, vk);
  const nu = P.length - 1, nv = P[0].length - 1;
  const umin = U[du], umax = U[nu + 1], vmin = V[dv], vmax = V[nv + 1];
  let closedU = false, closedV = false;
  const wrapIn = (x, lo, hi) => lo + ((((x - lo) % (hi - lo)) + (hi - lo)) % (hi - lo));
  const point = (u, v) => {
    u = closedU ? wrapIn(u, umin, umax) : Math.min(umax, Math.max(umin, u));
    v = closedV ? wrapIn(v, vmin, vmax) : Math.min(vmax, Math.max(vmin, v));
    const su = findSpan(nu, du, u, U), sv = findSpan(nv, dv, v, V);
    const Nu = basisFuns(su, u, du, U), Nv = basisFuns(sv, v, dv, V);
    let x = 0, y = 0, z = 0, w = 0;
    for (let i = 0; i <= du; i++) {
      const ii = su - du + i;
      for (let j = 0; j <= dv; j++) {
        const jj = sv - dv + j;
        const wk = W ? W[ii][jj] : 1;
        const b = Nu[i] * Nv[j] * wk;
        const c = P[ii][jj];
        x += b * c[0]; y += b * c[1]; z += b * c[2]; w += b;
      }
    }
    return [x / w, y / w, z / w];
  };
  // A direction is closed when its two boundary iso-curves coincide (e.g. a revolved NURBS).
  let gapU = 0, gapV = 0;
  for (let k = 0; k <= 8; k++) {
    gapU = Math.max(gapU, dist(point(umin, vmin + ((vmax - vmin) * k) / 8), point(umax, vmin + ((vmax - vmin) * k) / 8)));
    gapV = Math.max(gapV, dist(point(umin + ((umax - umin) * k) / 8, vmin), point(umin + ((umax - umin) * k) / 8, vmax)));
  }
  closedU = gapU < 1e-4;
  closedV = gapV < 1e-4;
  // grid for inversion and for scaling the parameter domain
  const G = 32;
  const grid = [];
  for (let i = 0; i <= G; i++) for (let j = 0; j <= G; j++) {
    const u = umin + ((umax - umin) * i) / G, v = vmin + ((vmax - vmin) * j) / G;
    grid.push([u, v, point(u, v)]);
  }
  let lu = 0, lv = 0;
  for (let i = 0; i <= G; i++) for (let j = 0; j < G; j++) {
    lv += dist(grid[i * (G + 1) + j][2], grid[i * (G + 1) + j + 1][2]);
    lu += dist(grid[j * (G + 1) + i][2], grid[(j + 1) * (G + 1) + i][2]);
  }
  lu /= G + 1; lv /= G + 1;
  const hu = 1e-5 * (umax - umin), hv = 1e-5 * (vmax - vmin);
  const keepU = (u) => (closedU ? u : Math.min(umax, Math.max(umin, u)));
  const keepV = (v) => (closedV ? v : Math.min(vmax, Math.max(vmin, v)));
  // one-sided differences at the edges of an open domain
  const du_ = (u, v) => {
    const a = closedU ? u - hu : Math.max(umin, u - hu), b = closedU ? u + hu : Math.min(umax, u + hu);
    return scl(sub(point(b, v), point(a, v)), 1 / (b - a));
  };
  const dv_ = (u, v) => {
    const a = closedV ? v - hv : Math.max(vmin, v - hv), b = closedV ? v + hv : Math.min(vmax, v + hv);
    return scl(sub(point(u, b), point(u, a)), 1 / (b - a));
  };
  return {
    kind: "bspline", point,
    periodicU: closedU, periodicV: closedV, periodU: umax - umin, periodV: vmax - vmin,
    scaleU: lu / (umax - umin || 1), scaleV: lv / (vmax - vmin || 1),
    param: (p) => {
      let best = grid[0], bestD = Infinity;
      for (const g of grid) { const d = dist(g[2], p); if (d < bestD) { bestD = d; best = g; } }
      let u = best[0], v = best[1], err = bestD;
      for (let it = 0; it < 40 && err > 1e-9; it++) {
        const su = du_(u, v), sv = dv_(u, v);
        const r = sub(p, point(u, v));
        const a11 = dot(su, su), a12 = dot(su, sv), a22 = dot(sv, sv);
        const b1 = dot(su, r), b2 = dot(sv, r);
        const det = a11 * a22 - a12 * a12;
        if (Math.abs(det) < 1e-24) break;
        const dU = (a22 * b1 - a12 * b2) / det, dV = (a11 * b2 - a12 * b1) / det;
        // damped Newton: only accept steps that get closer
        let t = 1, moved = false;
        for (let k = 0; k < 12; k++, t *= 0.5) {
          const nu2 = keepU(u + t * dU), nv2 = keepV(v + t * dV);
          const e2 = dist(p, point(nu2, nv2));
          if (e2 < err) { u = nu2; v = nv2; err = e2; moved = true; break; }
        }
        if (!moved) break;
      }
      if (closedU) u = wrapIn(u, umin, umax);
      if (closedV) v = wrapIn(v, vmin, vmax);
      return [u, v];
    },
    normal: (u, v) => numericNormal(point, u, v, hu * 10, hv * 10),
  };
}

function numericNormal(point, u, v, hu, hv) {
  // At a pole (where a revolved surface pinches to its axis) the derivatives
  // vanish, so step slightly into the surface until the normal is defined.
  const offsets = [[0, 0], [0, 40], [0, -40], [40, 0], [-40, 0], [40, 40], [-40, -40], [400, 0], [0, 400], [-400, 0], [0, -400]];
  for (const [ku, kv] of offsets) {
    const uu = u + ku * hu, vv = v + kv * hv;
    const su = sub(point(uu + hu, vv), point(uu - hu, vv));
    const sv = sub(point(uu, vv + hv), point(uu, vv - hv));
    const n = cross(su, sv);
    const l = len(n);
    if (l > 1e-9 * (len(su) * len(sv) + 1e-30)) return scl(n, 1 / l);
  }
  return [0, 0, 1];
}

/* ------------------------------------------------------------------------ */
/* Ear clipping with holes (after Mapbox earcut)                            */
/* ------------------------------------------------------------------------ */
function earcut(data, holeIndices) {
  const hasHoles = holeIndices && holeIndices.length;
  const outerLen = hasHoles ? holeIndices[0] * 2 : data.length;
  let outerNode = linkedList(data, 0, outerLen, true);
  const triangles = [];
  if (!outerNode || outerNode.next === outerNode.prev) return triangles;
  if (hasHoles) outerNode = eliminateHoles(data, holeIndices, outerNode);
  let minX = 0, minY = 0, invSize = 0;
  if (data.length > 80 * 2) {
    minX = data[0]; minY = data[1];
    let maxX = minX, maxY = minY;
    for (let i = 2; i < outerLen; i += 2) {
      const x = data[i], y = data[i + 1];
      if (x < minX) minX = x; if (y < minY) minY = y;
      if (x > maxX) maxX = x; if (y > maxY) maxY = y;
    }
    invSize = Math.max(maxX - minX, maxY - minY);
    invSize = invSize !== 0 ? 32767 / invSize : 0;
  }
  earcutLinked(outerNode, triangles, minX, minY, invSize, 0);
  return triangles;
}
function linkedList(data, start, end, clockwise) {
  let last = null;
  if (clockwise === signedArea(data, start, end) > 0) {
    for (let i = start; i < end; i += 2) last = insertNode(i / 2, data[i], data[i + 1], last);
  } else {
    for (let i = end - 2; i >= start; i -= 2) last = insertNode(i / 2, data[i], data[i + 1], last);
  }
  if (last && equals(last, last.next)) { removeNode(last); last = last.next; }
  return last;
}
function filterPoints(start, end) {
  if (!start) return start;
  if (!end) end = start;
  let p = start, again;
  do {
    again = false;
    if (!p.steiner && (equals(p, p.next) || area(p.prev, p, p.next) === 0)) {
      removeNode(p);
      p = end = p.prev;
      if (p === p.next) break;
      again = true;
    } else {
      p = p.next;
    }
  } while (again || p !== end);
  return end;
}
function earcutLinked(ear, triangles, minX, minY, invSize, pass) {
  if (!ear) return;
  if (!pass && invSize) indexCurve(ear, minX, minY, invSize);
  let stop = ear;
  while (ear.prev !== ear.next) {
    const prev = ear.prev, next = ear.next;
    if (invSize ? isEarHashed(ear, minX, minY, invSize) : isEar(ear)) {
      triangles.push(prev.i, ear.i, next.i);
      removeNode(ear);
      ear = next.next;
      stop = next.next;
      continue;
    }
    ear = next;
    if (ear === stop) {
      if (!pass) earcutLinked(filterPoints(ear), triangles, minX, minY, invSize, 1);
      else if (pass === 1) earcutLinked(cureLocalIntersections(filterPoints(ear), triangles), triangles, minX, minY, invSize, 2);
      else if (pass === 2) splitEarcut(ear, triangles, minX, minY, invSize);
      break;
    }
  }
}
function isEar(ear) {
  const a = ear.prev, b = ear, c = ear.next;
  if (area(a, b, c) >= 0) return false;
  const x0 = Math.min(a.x, b.x, c.x), y0 = Math.min(a.y, b.y, c.y);
  const x1 = Math.max(a.x, b.x, c.x), y1 = Math.max(a.y, b.y, c.y);
  let p = c.next;
  while (p !== a) {
    if (p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1 &&
        pointInTriangle(a.x, a.y, b.x, b.y, c.x, c.y, p.x, p.y) && area(p.prev, p, p.next) >= 0) return false;
    p = p.next;
  }
  return true;
}
function isEarHashed(ear, minX, minY, invSize) {
  const a = ear.prev, b = ear, c = ear.next;
  if (area(a, b, c) >= 0) return false;
  const x0 = Math.min(a.x, b.x, c.x), y0 = Math.min(a.y, b.y, c.y);
  const x1 = Math.max(a.x, b.x, c.x), y1 = Math.max(a.y, b.y, c.y);
  const minZ = zOrder(x0, y0, minX, minY, invSize), maxZ = zOrder(x1, y1, minX, minY, invSize);
  let p = ear.prevZ, n = ear.nextZ;
  const blocks = (q) => q !== a && q !== c && q.x >= x0 && q.x <= x1 && q.y >= y0 && q.y <= y1 &&
    pointInTriangle(a.x, a.y, b.x, b.y, c.x, c.y, q.x, q.y) && area(q.prev, q, q.next) >= 0;
  while (p && p.z >= minZ && n && n.z <= maxZ) {
    if (blocks(p)) return false;
    p = p.prevZ;
    if (blocks(n)) return false;
    n = n.nextZ;
  }
  while (p && p.z >= minZ) { if (blocks(p)) return false; p = p.prevZ; }
  while (n && n.z <= maxZ) { if (blocks(n)) return false; n = n.nextZ; }
  return true;
}
function cureLocalIntersections(start, triangles) {
  let p = start;
  do {
    const a = p.prev, b = p.next.next;
    if (!equals(a, b) && intersects(a, p, p.next, b) && locallyInside(a, b) && locallyInside(b, a)) {
      triangles.push(a.i, p.i, b.i);
      removeNode(p);
      removeNode(p.next);
      p = start = b;
    }
    p = p.next;
  } while (p !== start);
  return filterPoints(p);
}
function splitEarcut(start, triangles, minX, minY, invSize) {
  let a = start;
  do {
    let b = a.next.next;
    while (b !== a.prev) {
      if (a.i !== b.i && isValidDiagonal(a, b)) {
        let c = splitPolygon(a, b);
        a = filterPoints(a, a.next);
        c = filterPoints(c, c.next);
        earcutLinked(a, triangles, minX, minY, invSize, 0);
        earcutLinked(c, triangles, minX, minY, invSize, 0);
        return;
      }
      b = b.next;
    }
    a = a.next;
  } while (a !== start);
}
function eliminateHoles(data, holeIndices, outerNode) {
  const queue = [];
  for (let i = 0; i < holeIndices.length; i++) {
    const start = holeIndices[i] * 2;
    const end = i < holeIndices.length - 1 ? holeIndices[i + 1] * 2 : data.length;
    const list = linkedList(data, start, end, false);
    if (!list) continue;
    if (list === list.next) list.steiner = true;
    queue.push(getLeftmost(list));
  }
  queue.sort((a, b) => a.x - b.x || a.y - b.y);
  for (const hole of queue) outerNode = eliminateHole(hole, outerNode);
  return outerNode;
}
function eliminateHole(hole, outerNode) {
  const bridge = findHoleBridge(hole, outerNode);
  if (!bridge) return outerNode;
  const bridgeReverse = splitPolygon(bridge, hole);
  filterPoints(bridgeReverse, bridgeReverse.next);
  return filterPoints(bridge, bridge.next);
}
function findHoleBridge(hole, outerNode) {
  let p = outerNode;
  const hx = hole.x, hy = hole.y;
  let qx = -Infinity, m = null;
  do {
    if (hy <= p.y && hy >= p.next.y && p.next.y !== p.y) {
      const x = p.x + ((hy - p.y) * (p.next.x - p.x)) / (p.next.y - p.y);
      if (x <= hx && x > qx) {
        qx = x;
        m = p.x < p.next.x ? p : p.next;
        if (x === hx) return m;
      }
    }
    p = p.next;
  } while (p !== outerNode);
  if (!m) return null;
  const stop = m, mx = m.x, my = m.y;
  let tanMin = Infinity;
  p = m;
  do {
    if (hx >= p.x && p.x >= mx && hx !== p.x &&
        pointInTriangle(hy < my ? hx : qx, hy, mx, my, hy < my ? qx : hx, hy, p.x, p.y)) {
      const tan = Math.abs(hy - p.y) / (hx - p.x);
      if (locallyInside(p, hole) && (tan < tanMin || (tan === tanMin && (p.x > m.x || (p.x === m.x && sectorContainsSector(m, p)))))) {
        m = p;
        tanMin = tan;
      }
    }
    p = p.next;
  } while (p !== stop);
  return m;
}
const sectorContainsSector = (m, p) => area(m.prev, m, p.prev) < 0 && area(p.next, m, m.next) < 0;
function indexCurve(start, minX, minY, invSize) {
  let p = start;
  do {
    if (p.z === 0) p.z = zOrder(p.x, p.y, minX, minY, invSize);
    p.prevZ = p.prev;
    p.nextZ = p.next;
    p = p.next;
  } while (p !== start);
  p.prevZ.nextZ = null;
  p.prevZ = null;
  sortLinked(p);
}
function sortLinked(list) {
  let inSize = 1, numMerges;
  do {
    let p = list, e, tail = null;
    list = null;
    numMerges = 0;
    while (p) {
      numMerges++;
      let q = p, pSize = 0;
      for (let i = 0; i < inSize; i++) { pSize++; q = q.nextZ; if (!q) break; }
      let qSize = inSize;
      while (pSize > 0 || (qSize > 0 && q)) {
        if (pSize !== 0 && (qSize === 0 || !q || p.z <= q.z)) { e = p; p = p.nextZ; pSize--; }
        else { e = q; q = q.nextZ; qSize--; }
        if (tail) tail.nextZ = e; else list = e;
        e.prevZ = tail;
        tail = e;
      }
      p = q;
    }
    tail.nextZ = null;
    inSize *= 2;
  } while (numMerges > 1);
  return list;
}
function zOrder(x, y, minX, minY, invSize) {
  x = ((x - minX) * invSize) | 0;
  y = ((y - minY) * invSize) | 0;
  x = (x | (x << 8)) & 0x00ff00ff; x = (x | (x << 4)) & 0x0f0f0f0f; x = (x | (x << 2)) & 0x33333333; x = (x | (x << 1)) & 0x55555555;
  y = (y | (y << 8)) & 0x00ff00ff; y = (y | (y << 4)) & 0x0f0f0f0f; y = (y | (y << 2)) & 0x33333333; y = (y | (y << 1)) & 0x55555555;
  return x | (y << 1);
}
function getLeftmost(start) {
  let p = start, leftmost = start;
  do {
    if (p.x < leftmost.x || (p.x === leftmost.x && p.y < leftmost.y)) leftmost = p;
    p = p.next;
  } while (p !== start);
  return leftmost;
}
const pointInTriangle = (ax, ay, bx, by, cx, cy, px, py) =>
  (cx - px) * (ay - py) >= (ax - px) * (cy - py) &&
  (ax - px) * (by - py) >= (bx - px) * (ay - py) &&
  (bx - px) * (cy - py) >= (cx - px) * (by - py);
function isValidDiagonal(a, b) {
  return a.next.i !== b.i && a.prev.i !== b.i && !intersectsPolygon(a, b) &&
    ((locallyInside(a, b) && locallyInside(b, a) && middleInside(a, b) && (area(a.prev, a, b.prev) || area(a, b.prev, b))) ||
      (equals(a, b) && area(a.prev, a, a.next) > 0 && area(b.prev, b, b.next) > 0));
}
const area = (p, q, r) => (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y);
const equals = (p1, p2) => p1.x === p2.x && p1.y === p2.y;
const sign = (v) => (v > 0 ? 1 : v < 0 ? -1 : 0);
const onSegment = (p, q, r) => q.x <= Math.max(p.x, r.x) && q.x >= Math.min(p.x, r.x) && q.y <= Math.max(p.y, r.y) && q.y >= Math.min(p.y, r.y);
function intersects(p1, q1, p2, q2) {
  const o1 = sign(area(p1, q1, p2)), o2 = sign(area(p1, q1, q2)), o3 = sign(area(p2, q2, p1)), o4 = sign(area(p2, q2, q1));
  if (o1 !== o2 && o3 !== o4) return true;
  if (o1 === 0 && onSegment(p1, p2, q1)) return true;
  if (o2 === 0 && onSegment(p1, q2, q1)) return true;
  if (o3 === 0 && onSegment(p2, p1, q2)) return true;
  if (o4 === 0 && onSegment(p2, q1, q2)) return true;
  return false;
}
function intersectsPolygon(a, b) {
  let p = a;
  do {
    if (p.i !== a.i && p.next.i !== a.i && p.i !== b.i && p.next.i !== b.i && intersects(p, p.next, a, b)) return true;
    p = p.next;
  } while (p !== a);
  return false;
}
function locallyInside(a, b) {
  return area(a.prev, a, a.next) < 0
    ? area(a, b, a.next) >= 0 && area(a, a.prev, b) >= 0
    : area(a, b, a.prev) < 0 || area(a, a.next, b) < 0;
}
function middleInside(a, b) {
  let p = a, inside = false;
  const px = (a.x + b.x) / 2, py = (a.y + b.y) / 2;
  do {
    if (p.y > py !== p.next.y > py && p.next.y !== p.y && px < ((p.next.x - p.x) * (py - p.y)) / (p.next.y - p.y) + p.x) inside = !inside;
    p = p.next;
  } while (p !== a);
  return inside;
}
function splitPolygon(a, b) {
  const a2 = new EarNode(a.i, a.x, a.y), b2 = new EarNode(b.i, b.x, b.y);
  const an = a.next, bp = b.prev;
  a.next = b; b.prev = a;
  a2.next = an; an.prev = a2;
  b2.next = a2; a2.prev = b2;
  bp.next = b2; b2.prev = bp;
  return b2;
}
function insertNode(i, x, y, last) {
  const p = new EarNode(i, x, y);
  if (!last) { p.prev = p; p.next = p; }
  else { p.next = last.next; p.prev = last; last.next.prev = p; last.next = p; }
  return p;
}
function removeNode(p) {
  p.next.prev = p.prev;
  p.prev.next = p.next;
  if (p.prevZ) p.prevZ.nextZ = p.nextZ;
  if (p.nextZ) p.nextZ.prevZ = p.prevZ;
}
class EarNode {
  constructor(i, x, y) {
    this.i = i; this.x = x; this.y = y;
    this.prev = null; this.next = null;
    this.z = 0; this.prevZ = null; this.nextZ = null;
    this.steiner = false;
  }
}
function signedArea(data, start, end) {
  let sum = 0;
  for (let i = start, j = end - 2; i < end; i += 2) {
    sum += (data[j] - data[i]) * (data[i + 1] + data[j + 1]);
    j = i;
  }
  return sum;
}

/* ------------------------------------------------------------------------ */
/* Face tessellation                                                        */
/* ------------------------------------------------------------------------ */
const loopArea = (pts2) => {
  let s = 0;
  for (let i = 0, j = pts2.length - 1; i < pts2.length; j = i++) s += (pts2[j][0] - pts2[i][0]) * (pts2[j][1] + pts2[i][1]);
  return -s / 2;
};
const wrapPi = (a) => { while (a > Math.PI) a -= TWO_PI; while (a <= -Math.PI) a += TWO_PI; return a; };

function faceLoops(bounds) {
  const loops = [];
  for (const b of bounds) {
    const be = ent(b.id);
    const le = ent(be.args[1].id);
    if (le.type !== "EDGE_LOOP") continue; // VERTEX_LOOP / POLY_LOOP: nothing to fill
    const pts = [];
    for (const oe of le.args[1]) {
      const oa = args(oe.id);
      let ep = edgePoints(oa[3].id);
      if (oa[4] !== ".T.") ep = ep.slice().reverse();
      for (let k = 0; k < ep.length - 1; k++) pts.push(ep[k]);
    }
    if (pts.length >= 2) loops.push({ pts, outer: be.type === "FACE_OUTER_BOUND" });
  }
  return loops;
}

// Returns { pos: [[x,y,z]], nrm: [[x,y,z]], tri: [i,j,k,...] } or null
function tessellateFace(fid) {
  const a = args(fid); // ADVANCED_FACE(name, bounds, surface, same_sense)
  const sense = a[3] === ".T.";
  const surf = surfaceOf(a[2].id);
  if (!surf) { warn("unsupported surface " + (typeOf(a[2].id) || "complex")); return null; }
  const loops = faceLoops(a[1]);
  if (!loops.length) return null;

  // parameter-space polygons
  for (const L of loops) L.uv = L.pts.map((p) => surf.param(p));
  if (surf.isPole) {
    for (const L of loops) {
      const n = L.pts.length;
      if (!L.pts.some((p) => surf.isPole(p))) continue;
      const pts = [], uv = [];
      L.poleAt = [];
      for (let i = 0; i < n; i++) {
        if (!surf.isPole(L.pts[i])) { pts.push(L.pts[i]); uv.push(L.uv[i]); continue; }
        const before = L.uv[(i - 1 + n) % n], after = L.uv[(i + 1) % n];
        pts.push(L.pts[i], L.pts[i]);
        uv.push([before[0], L.uv[i][1]], [after[0], L.uv[i][1]]);
        L.poleAt.push(uv.length - 1);
      }
      L.pts = pts;
      L.uv = uv;
    }
  }
  const periodOf = (k) => (k === 0 ? surf.periodU : surf.periodV) || TWO_PI;
  const wrapP = (d, T) => d - T * Math.round(d / T);
  if (surf.periodicU || surf.periodicV) {
    for (const L of loops) {
      for (const k of [0, 1]) {
        if (k === 0 ? !surf.periodicU : !surf.periodicV) continue;
        const T = periodOf(k);
        for (let i = 1; i < L.uv.length; i++) {
          L.uv[i][k] = L.uv[i - 1][k] + wrapP(L.uv[i][k] - L.uv[i - 1][k], T);
        }
        const winding = L.uv[L.uv.length - 1][k] + wrapP(L.uv[0][k] - L.uv[L.uv.length - 1][k], T) - L.uv[0][k];
        if (Math.abs(winding) > T / 2) {
          // crossing a pole can jump a whole turn in longitude; take the jump at the pole
          if (k === 0 && L.poleAt && L.poleAt.length) for (let i = L.poleAt[0]; i < L.uv.length; i++) L.uv[i][0] -= winding;
          else L.wraps = true;
        }
      }
    }
    if (loops.some((L) => L.wraps)) {
      const banded = bandLoops(loops, surf);
      if (!banded) { warn("face with a loop around a periodic surface"); return null; }
      loops.length = 0;
      loops.push(...banded);
    }
  }

  // decide which loop is the outer boundary
  let outerIdx = loops.findIndex((L) => L.outer);
  if (outerIdx < 0 || loops.length > 1) {
    let best = -1, bestA = -1;
    loops.forEach((L, i) => { const A = Math.abs(loopArea(L.uv)); if (A > bestA) { bestA = A; best = i; } });
    if (outerIdx < 0) outerIdx = best;
  }
  const outer = loops[outerIdx];
  const holes = loops.filter((_, i) => i !== outerIdx);

  // bring holes on periodic surfaces into the outer loop's period
  if (surf.periodicU || surf.periodicV) {
    for (const k of [0, 1]) {
      if (k === 0 ? !surf.periodicU : !surf.periodicV) continue;
      let lo = Infinity, hi = -Infinity;
      for (const p of outer.uv) { lo = Math.min(lo, p[k]); hi = Math.max(hi, p[k]); }
      const centre = (lo + hi) / 2;
      for (const H of holes) {
        let m = 0;
        for (const p of H.uv) m += p[k];
        m /= H.uv.length;
        const shift = Math.round((centre - m) / periodOf(k)) * periodOf(k);
        if (shift) for (const p of H.uv) p[k] += shift;
      }
    }
  }

  const all = [outer, ...holes];
  const P = [];
  const UV = [];
  for (const L of all) for (let i = 0; i < L.pts.length; i++) { P.push(L.pts[i]); UV.push(L.uv[i]); }
  const su = surf.scaleU || 1, sv = surf.scaleV || 1;

  let tri = null;
  if (!surf.planar && holes.length === 0) tri = gridStrip(P, UV, surf);
  if (!tri) {
    const flat = [];
    const holeIdx = [];
    let count = 0;
    for (const L of all) {
      if (L !== outer) holeIdx.push(count);
      for (const p of L.uv) { flat.push(p[0] * su, p[1] * sv); count++; }
    }
    tri = earcut(flat, holeIdx);
  }
  // make every triangle counter-clockwise in (u, v)
  for (let t = 0; t < tri.length; t += 3) {
    const A = UV[tri[t]], B = UV[tri[t + 1]], C = UV[tri[t + 2]];
    const s = ((B[0] - A[0]) * su) * ((C[1] - A[1]) * sv) - ((C[0] - A[0]) * su) * ((B[1] - A[1]) * sv);
    if (s < 0) { const tmp = tri[t + 1]; tri[t + 1] = tri[t + 2]; tri[t + 2] = tmp; }
  }

  let nrm;
  if (surf.planar) {
    nrm = P.map(() => surf.normal());
  } else {
    const boundary = new Set();
    let base = 0;
    for (const L of all) {
      const n = L.pts.length;
      for (let i = 0; i < n; i++) boundary.add(edgeKey(base + i, base + ((i + 1) % n)));
      base += n;
    }
    nrm = UV.map((q) => surf.normal(q[0], q[1]));
    tri = refine(surf, P, UV, nrm, tri, boundary);
  }
  if (!sense) {
    nrm = nrm.map((n) => [-n[0], -n[1], -n[2]]);
    for (let t = 0; t < tri.length; t += 3) { const tmp = tri[t + 1]; tri[t + 1] = tri[t + 2]; tri[t + 2] = tmp; }
  }
  return { pos: P, nrm, tri };
}

const edgeKey = (i, j) => (i < j ? i * 4194304 + j : j * 4194304 + i);

// Stitch two rows of vertex indices (each sorted by u) into a strip of triangles.
function stitch(A, B, UV, tri) {
  let i = 0, j = 0;
  while (i < A.length - 1 || j < B.length - 1) {
    if (i < A.length - 1 && (j >= B.length - 1 || UV[A[i + 1]][0] <= UV[B[j + 1]][0])) { tri.push(A[i], A[i + 1], B[j]); i++; }
    else { tri.push(A[i], B[j + 1], B[j]); j++; }
  }
}
function uniqSorted(values, eps) {
  const out = [];
  for (const x of values.slice().sort((a, b) => a - b)) if (!out.length || x - out[out.length - 1] > eps) out.push(x);
  return out;
}

// Curved faces whose boundary is a rectangle in (u, v) -- fillets, cans, bands,
// lead bends -- get rows of points stitched together instead of a fan of slivers.
// Boundary points are reused exactly, so neighbouring faces still meet crack-free.
function gridStrip(P, UV, surf) {
  let ulo = Infinity, uhi = -Infinity, vlo = Infinity, vhi = -Infinity;
  for (const q of UV) { ulo = Math.min(ulo, q[0]); uhi = Math.max(uhi, q[0]); vlo = Math.min(vlo, q[1]); vhi = Math.max(vhi, q[1]); }
  if (!(uhi > ulo) || !(vhi > vlo)) return null;
  const eu = 1e-6 + 1e-5 * (uhi - ulo), ev = 1e-6 + 1e-5 * (vhi - vlo);
  const bottom = [], top = [], left = [], right = [];
  for (let i = 0; i < UV.length; i++) {
    const [u, v] = UV[i];
    let onSide = false;
    if (Math.abs(v - vlo) < ev) { bottom.push(i); onSide = true; }
    if (Math.abs(v - vhi) < ev) { top.push(i); onSide = true; }
    if (Math.abs(u - ulo) < eu) { left.push(i); onSide = true; }
    if (Math.abs(u - uhi) < eu) { right.push(i); onSide = true; }
    if (!onSide) return null;
  }
  const levelsL = uniqSorted(left.map((i) => UV[i][1]), ev);
  const levelsR = uniqSorted(right.map((i) => UV[i][1]), ev);
  if (levelsL.length < 2 || levelsL.length !== levelsR.length) return null;
  for (let k = 0; k < levelsL.length; k++) if (Math.abs(levelsL[k] - levelsR[k]) > 10 * ev) return null;
  const byU = (a, b) => UV[a][0] - UV[b][0];
  const atLevel = (ids, v) => ids.find((i) => Math.abs(UV[i][1] - v) < ev);
  const cols = uniqSorted(bottom.concat(top).map((i) => UV[i][0]), eu).filter((u) => u > ulo + eu && u < uhi - eu);
  const rows = [];
  for (let k = 0; k < levelsL.length; k++) {
    const v = levelsL[k];
    if (k === 0) { rows.push(bottom.slice().sort(byU)); continue; }
    if (k === levelsL.length - 1) { rows.push(top.slice().sort(byU)); continue; }
    const row = [atLevel(left, v)];
    for (const u of cols) { row.push(P.length); P.push(surf.point(u, v)); UV.push([u, v]); }
    row.push(atLevel(right, levelsR[k]));
    rows.push(row);
  }
  const tri = [];
  for (let k = 0; k + 1 < rows.length; k++) stitch(rows[k], rows[k + 1], UV, tri);
  return tri;
}

// Turn loops that wrap all the way around a periodic surface into one band polygon.
function bandLoops(loops, surf) {
  const wrapping = loops.filter((L) => L.wraps);
  if (wrapping.length !== 2 || !surf.periodicU) return null;
  const cut = (L) => {
    const n = L.uv.length;
    const uv = L.uv.map((q) => q.slice());
    const pts = L.pts.slice();
    // close the loop and make u increase
    const last = uv[n - 1][0] + wrapPi(uv[0][0] - uv[n - 1][0]);
    const inc = last + 0 > uv[0][0];
    let seq = uv.map((q, i) => ({ q, p: pts[i] }));
    seq.push({ q: [last, uv[0][1]], p: pts[0] });
    if (!inc) seq.reverse();
    const start = seq[0].q[0];
    return seq.map((s) => ({ q: [s.q[0] - start, s.q[1]], p: s.p }));
  };
  const a = cut(wrapping[0]);
  const b = cut(wrapping[1]);
  // align b's starting angle with a's
  const raw = (s) => Math.atan2(Math.sin(s), Math.cos(s));
  const aStart = raw(surf.param(a[0].p)[0]);
  const bStart = raw(surf.param(b[0].p)[0]);
  const off = ((bStart - aStart) % TWO_PI + TWO_PI) % TWO_PI;
  const bq = b.map((s) => ({ q: [s.q[0] + off, s.q[1]], p: s.p }));
  // rotate b so it spans [0, 2pi] like a
  const pts = [], uv = [];
  for (const s of a) { pts.push(s.p); uv.push(s.q); }
  const bb = [];
  for (const s of bq) bb.push(s.q[0] > TWO_PI ? { q: [s.q[0] - TWO_PI, s.q[1]], p: s.p } : s);
  bb.sort((x, y) => y.q[0] - x.q[0]);
  const top = bb[0].q[0] < TWO_PI - 1e-9 ? [{ q: [TWO_PI, bb[bb.length - 1].q[1]], p: bb[bb.length - 1].p }, ...bb] : bb;
  if (top[top.length - 1].q[0] > 1e-9) top.push({ q: [0, top[0].q[1]], p: top[0].p });
  for (const s of top) { pts.push(s.p); uv.push(s.q); }
  return [{ pts, uv, outer: true }];
}

// Split interior edges until the surface is followed within TOL and normals turn gently.
function refine(surf, P, UV, N, tri, boundary) {
  const maxAngle = MAX_STEP;
  const minEdge = 4 * TOL;
  // Q: the surface evaluated at each vertex's parameters. Boundary points come
  // from the edge curves and can sit a hair off the surface (CAD tolerance);
  // measuring against Q keeps that gap from driving endless refinement.
  const Q = UV.map((q, i) => (i < P.length ? surf.point(q[0], q[1]) : P[i]));
  for (let pass = 0; pass < 8; pass++) {
    const mids = new Map();
    for (let t = 0; t < tri.length; t += 3) {
      for (let e = 0; e < 3; e++) {
        const i = tri[t + e], j = tri[t + ((e + 1) % 3)];
        const key = edgeKey(i, j);
        if (boundary.has(key) || mids.has(key)) continue;
        const qi = UV[i], qj = UV[j];
        const mu = (qi[0] + qj[0]) / 2, mv = (qi[1] + qj[1]) / 2;
        let split = false;
        let pm = null;
        if (dist(Q[i], Q[j]) > minEdge) {
          if (surf.angularU && Math.abs(qi[0] - qj[0]) > maxAngle) split = true;
          if (surf.angularV && Math.abs(qi[1] - qj[1]) > maxAngle) split = true;
          if (!split) {
            pm = surf.point(mu, mv);
            if (dist(pm, mix(Q[i], Q[j], 0.5)) > REFINE_TOL()) split = true;
            else if (dot(N[i], N[j]) < MAX_NORMAL_TURN) split = true;
          }
        }
        if (split) {
          const k = P.length;
          if (!pm) pm = surf.point(mu, mv);
          P.push(pm);
          Q.push(pm);
          UV.push([mu, mv]);
          N.push(surf.normal(mu, mv));
          mids.set(key, k);
        } else {
          mids.set(key, -1);
        }
      }
    }
    let any = false;
    for (const v of mids.values()) if (v >= 0) { any = true; break; }
    if (!any) break;
    const out = [];
    for (let t = 0; t < tri.length; t += 3) {
      const a = tri[t], b = tri[t + 1], c = tri[t + 2];
      const ab = mids.get(edgeKey(a, b)), bc = mids.get(edgeKey(b, c)), ca = mids.get(edgeKey(c, a));
      const mab = ab !== undefined && ab >= 0 ? ab : -1;
      const mbc = bc !== undefined && bc >= 0 ? bc : -1;
      const mca = ca !== undefined && ca >= 0 ? ca : -1;
      const n = (mab >= 0) + (mbc >= 0) + (mca >= 0);
      if (n === 0) out.push(a, b, c);
      else if (n === 3) out.push(a, mab, mca, mab, b, mbc, mca, mbc, c, mab, mbc, mca);
      else if (n === 1) {
        if (mab >= 0) out.push(a, mab, c, mab, b, c);
        else if (mbc >= 0) out.push(a, b, mbc, a, mbc, c);
        else out.push(a, b, mca, mca, b, c);
      } else {
        // two split edges: rotate so the unsplit edge is (c, a) ... handle each case
        if (mca < 0) out.push(a, mab, c, mab, mbc, c, mab, b, mbc);
        else if (mab < 0) out.push(a, b, mbc, a, mbc, mca, mca, mbc, c);
        else out.push(a, mab, mca, mab, b, mca, b, c, mca);
      }
    }
    tri = out;
  }
  return tri;
}

/* ------------------------------------------------------------------------ */
/* Colours                                                                  */
/* ------------------------------------------------------------------------ */
const NAMED = { red: [1, 0, 0], green: [0, 1, 0], blue: [0, 0, 1], yellow: [1, 1, 0], magenta: [1, 0, 1], cyan: [0, 1, 1], black: [0, 0, 0], white: [1, 1, 1] };
function colourOfStyles(styleRefs) {
  const seen = new Set();
  const visit = (id) => {
    if (seen.has(id)) return null;
    seen.add(id);
    const e = E.get(id);
    if (!e) return null;
    if (e.type === "COLOUR_RGB") return [e.args[1], e.args[2], e.args[3]];
    if (e.type === "DRAUGHTING_PRE_DEFINED_COLOUR") return NAMED[e.args[0]] || null;
    if (e.type === "CURVE_STYLE") return null; // edge colours: ignore
    const list = e.type ? e.args : Object.values(e.parts).flat();
    const stack = [list];
    while (stack.length) {
      const v = stack.pop();
      if (v instanceof Ref) { const c = visit(v.id); if (c) return c; }
      else if (Array.isArray(v)) for (let i = v.length - 1; i >= 0; i--) stack.push(v[i]);
    }
    return null;
  };
  for (const r of styleRefs) { const c = visit(r.id); if (c) return c; }
  return null;
}
const faceColour = new Map();
const itemColour = new Map();
for (const [id, e] of E) {
  if (e.type !== "STYLED_ITEM" && e.type !== "OVER_RIDING_STYLED_ITEM") continue;
  const target = e.args[2];
  if (!(target instanceof Ref)) continue;
  const tt = typeOf(target.id);
  const c = colourOfStyles(e.args[1]);
  if (!c) continue;
  if (tt === "ADVANCED_FACE") faceColour.set(target.id, c);
  else if (tt !== "EDGE_CURVE" && tt !== "VERTEX_POINT") itemColour.set(target.id, c);
}
const palette = [];
const paletteIndex = new Map();
function shadeMask(c) {
  const [r, g, b] = c;
  if (!(g > r + 0.1 && g > b + 0.04)) return c; // only the mask greens
  return [r * MASK_SHADE, g * MASK_SHADE, Math.min(1, b * MASK_SHADE * 1.04)];
}
function colourIndex(c) {
  c = shadeMask(c);
  const key = c.map((x) => Math.round(Math.min(1, Math.max(0, x)) * 255)).join(",");
  let i = paletteIndex.get(key);
  if (i === undefined) {
    i = palette.length;
    palette.push(key.split(",").map(Number));
    paletteIndex.set(key, i);
  }
  return i;
}

/* ------------------------------------------------------------------------ */
/* Assembly: which representation goes where                                */
/* ------------------------------------------------------------------------ */
const children = new Map();
const isChild = new Set();
for (const [id, e] of E) {
  let rr = null, transform = null;
  if (e.type === "" && e.parts.REPRESENTATION_RELATIONSHIP) {
    rr = e.parts.REPRESENTATION_RELATIONSHIP;
    const w = e.parts.REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION;
    if (w) transform = w[0];
  } else if (e.type === "SHAPE_REPRESENTATION_RELATIONSHIP") {
    rr = e.args;
  } else continue;
  const child = rr[2].id, parent = rr[3].id;
  let m = IDENTITY;
  if (transform instanceof Ref && typeOf(transform.id) === "ITEM_DEFINED_TRANSFORMATION") {
    const ta = args(transform.id);
    m = matMul(placementMatrix(ta[3].id), matInvRigid(placementMatrix(ta[2].id)));
  }
  if (!children.has(parent)) children.set(parent, []);
  children.get(parent).push({ child, m });
  isChild.add(child);
}
const REP_TYPES = new Set(["SHAPE_REPRESENTATION", "ADVANCED_BREP_SHAPE_REPRESENTATION", "MANIFOLD_SURFACE_SHAPE_REPRESENTATION", "FACETED_BREP_SHAPE_REPRESENTATION"]);
const roots = [];
for (const [id, e] of E) if (REP_TYPES.has(e.type) && !isChild.has(id)) roots.push(id);

function geometryItems(rep) {
  const out = [];
  for (const r of args(rep)[1] || []) {
    const t = typeOf(r.id);
    if (t === "MANIFOLD_SOLID_BREP" || t === "SHELL_BASED_SURFACE_MODEL" || t === "BREP_WITH_VOIDS") out.push(r.id);
  }
  return out;
}
// product names, so parts can be found again later (e.g. "R_Axial_...")
const productOfRep = new Map();
for (const [, e] of E) {
  if (e.type !== "SHAPE_DEFINITION_REPRESENTATION") continue;
  try {
    const pd = ent(ent(e.args[0].id).args[2].id);
    const prod = ent(ent(pd.args[2].id).args[2].id);
    productOfRep.set(e.args[1].id, prod.args[1] || prod.args[0]);
  } catch (err) { /* not a product shape */ }
}
const instances = [];
function walk(rep, m, depth, name) {
  if (depth > 32) return;
  const nm = productOfRep.get(rep) || name;
  if (geometryItems(rep).length) instances.push({ rep, m, name: nm });
  for (const c of children.get(rep) || []) walk(c.child, matMul(m, c.m), depth + 1, nm);
}
for (const r of roots) walk(r, IDENTITY, 0, "");
console.log(`${roots.length} root representation(s), ${instances.length} placed parts`);

/* ------------------------------------------------------------------------ */
/* Tessellate each distinct representation once                             */
/* ------------------------------------------------------------------------ */
function shellFaces(item) {
  const e = ent(item);
  const shells = [];
  if (e.type === "MANIFOLD_SOLID_BREP" || e.type === "BREP_WITH_VOIDS") shells.push(e.args[1].id);
  if (e.type === "SHELL_BASED_SURFACE_MODEL") for (const s of e.args[1]) shells.push(s.id);
  const faces = [];
  for (const s of shells) for (const f of args(s)[1]) faces.push({ face: f.id, shell: s });
  return faces;
}

const meshes = [];
const meshOfRep = new Map();
let faceCount = 0, failed = 0;
const tTess = Date.now();
for (const inst of instances) {
  if (meshOfRep.has(inst.rep)) continue;
  const pos = [], nrm = [], col = [], idx = [];
  let overlay = false;
  const repName = String(args(inst.rep)[0] || "");
  const layer = /silk/i.test(repName) ? 3 : /pad|mask|paste/i.test(repName) ? 2 : /copper|\bcu\b/i.test(repName) ? 1 : 3;
  for (const item of geometryItems(inst.rep)) {
    if (typeOf(item) === "SHELL_BASED_SURFACE_MODEL") overlay = true;
    for (const { face, shell } of shellFaces(item)) {
      faceCount++;
      const c = faceColour.get(face) || itemColour.get(shell) || itemColour.get(item) || DEFAULT_COLOUR;
      const ci = colourIndex(c);
      let res = null;
      try { res = tessellateFace(face); } catch (err) { warn("face failed: " + err.message); }
      if (!res || !res.tri.length) { failed++; continue; }
      const base = pos.length / 3;
      for (let i = 0; i < res.pos.length; i++) {
        pos.push(res.pos[i][0], res.pos[i][1], res.pos[i][2]);
        nrm.push(res.nrm[i][0], res.nrm[i][1], res.nrm[i][2]);
        col.push(ci);
      }
      for (const k of res.tri) idx.push(base + k);
    }
  }
  meshOfRep.set(inst.rep, meshes.length);
  meshes.push({ pos, nrm, col, idx, overlay, layer });
}
console.log(`tessellated ${faceCount} faces (${failed} skipped) in ${((Date.now() - tTess) / 1000).toFixed(1)}s`);

/* ------------------------------------------------------------------------ */
/* Write                                                                    */
/* ------------------------------------------------------------------------ */
const chunksOf = (mesh) => {
  const chunks = [];
  let map = new Map(), verts = [], ind = [];
  const flush = () => { if (ind.length) chunks.push({ verts, ind }); map = new Map(); verts = []; ind = []; };
  for (let t = 0; t < mesh.idx.length; t += 3) {
    let need = 0;
    for (let e = 0; e < 3; e++) if (!map.has(mesh.idx[t + e])) need++;
    if (verts.length + need > 65535) flush();
    for (let e = 0; e < 3; e++) {
      const g = mesh.idx[t + e];
      let l = map.get(g);
      if (l === undefined) { l = verts.length; map.set(g, l); verts.push(g); }
      ind.push(l);
    }
  }
  flush();
  return chunks;
};

const parts = [];
let bytes = 0;
const pushBuf = (buf) => { parts.push(buf); bytes += buf.byteLength; };
const u32 = (...v) => pushBuf(Buffer.from(new Uint32Array(v).buffer));
const f32 = (...v) => pushBuf(Buffer.from(new Float32Array(v).buffer));
const pad4 = () => { const r = bytes % 4; if (r) pushBuf(Buffer.alloc(4 - r)); };

pushBuf(Buffer.from("PCB3", "latin1"));
u32(1);
u32(palette.length);
pushBuf(Buffer.from(palette.flatMap((c) => [c[0], c[1], c[2], 0])));

// world bounds from transformed mesh bounds
const wmin = [Infinity, Infinity, Infinity], wmax = [-Infinity, -Infinity, -Infinity];
const meshBounds = meshes.map((m) => {
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < m.pos.length; i += 3) for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], m.pos[i + k]); hi[k] = Math.max(hi[k], m.pos[i + k]); }
  return [lo, hi];
});
for (const inst of instances) {
  const [lo, hi] = meshBounds[meshOfRep.get(inst.rep)];
  if (!isFinite(lo[0])) continue;
  for (let c = 0; c < 8; c++) {
    const p = matPoint(inst.m, [c & 1 ? hi[0] : lo[0], c & 2 ? hi[1] : lo[1], c & 4 ? hi[2] : lo[2]]);
    for (let k = 0; k < 3; k++) { wmin[k] = Math.min(wmin[k], p[k]); wmax[k] = Math.max(wmax[k], p[k]); }
  }
}
f32(...wmin, ...wmax);

let totalTris = 0, totalVerts = 0;
u32(meshes.length);
meshes.forEach((m, mi) => {
  const [lo, hi] = meshBounds[mi];
  const step = [0, 1, 2].map((k) => Math.max(hi[k] - lo[k], 1e-9) / 65535);
  const chunks = m.idx.length ? chunksOf(m) : [];
  u32(m.overlay ? 1 | (m.layer << 1) : 0);
  f32(...(isFinite(lo[0]) ? lo : [0, 0, 0]), ...step);
  u32(chunks.length);
  for (const ch of chunks) {
    const n = ch.verts.length;
    u32(n, ch.ind.length);
    const qp = new Uint16Array(n * 3);
    const nc = new Int8Array(n * 4);
    for (let v = 0; v < n; v++) {
      const g = ch.verts[v];
      for (let k = 0; k < 3; k++) {
        qp[v * 3 + k] = Math.round((m.pos[g * 3 + k] - lo[k]) / step[k]);
        nc[v * 4 + k] = Math.max(-127, Math.min(127, Math.round(m.nrm[g * 3 + k] * 127)));
      }
      nc[v * 4 + 3] = m.col[g];
    }
    pushBuf(Buffer.from(qp.buffer)); pad4();
    pushBuf(Buffer.from(nc.buffer));
    pushBuf(Buffer.from(new Uint16Array(ch.ind).buffer)); pad4();
    totalTris += ch.ind.length / 3;
    totalVerts += n;
  }
});

u32(instances.length);
for (const inst of instances) {
  u32(meshOfRep.get(inst.rep));
  f32(...inst.m);
}

if (PARTS_OUT) {
  const list = instances.map((inst) => {
    const [lo, hi] = meshBounds[meshOfRep.get(inst.rep)];
    const wlo = [Infinity, Infinity, Infinity], whi = [-Infinity, -Infinity, -Infinity];
    for (let c = 0; c < 8 && isFinite(lo[0]); c++) {
      const p = matPoint(inst.m, [c & 1 ? hi[0] : lo[0], c & 2 ? hi[1] : lo[1], c & 4 ? hi[2] : lo[2]]);
      for (let k = 0; k < 3; k++) { wlo[k] = Math.min(wlo[k], p[k]); whi[k] = Math.max(whi[k], p[k]); }
    }
    return { name: inst.name, mesh: meshOfRep.get(inst.rep), m: inst.m, lo: wlo, hi: whi };
  });
  writeFileSync(PARTS_OUT, JSON.stringify(list));
}
const out = Buffer.concat(parts);
writeFileSync(OUTPUT, out);
// Browsers block data requests from pages opened straight from disk, so the
// viewer falls back to this script copy of the same bytes (base64) there.
const key = OUTPUT.split(/[\\/]/).pop();
writeFileSync(OUTPUT + ".js", `(window.__boardModels=window.__boardModels||{})[${JSON.stringify(key)}]="${out.toString("base64")}";\n`);
let placedTris = 0;
for (const inst of instances) placedTris += meshes[meshOfRep.get(inst.rep)].idx.length / 3;
console.log(`${meshes.length} meshes, ${totalVerts} vertices, ${totalTris} triangles stored (${placedTris} drawn), ${palette.length} colours`);
console.log(`bounds: [${wmin.map((x) => x.toFixed(2))}] .. [${wmax.map((x) => x.toFixed(2))}] mm`);
console.log(`wrote ${OUTPUT} (${(bytes / 1048576).toFixed(2)} MB) in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
for (const [msg, n] of warnings) console.warn(`warning: ${msg} (x${n})`);
