/*
  board-viewer.js
  ---------------
  A small, dependency-free WebGL 1 viewer for the board meshes written by
  tools/step-to-mesh.mjs. Written in plain ES5 and uses no WebGL extensions
  (16-bit index chunks), so it runs on old phones and old browsers too.

  Markup:
    <div class="board-viewer" data-board-viewer data-src="model.bin">
      <img class="board-viewer__poster" ...>       still image, shown until 3D is ready
      <canvas class="board-viewer__canvas" tabindex="0" ...>
      <p class="board-viewer__status" data-viewer-status>...</p>
      <button class="board-viewer__reset" data-viewer-reset hidden>Reset view</button>
    </div>

  Behaviour: the model spins slowly until someone drags, scrolls, pinches or
  presses a key on it. Then: drag (one finger) orbits; right-drag, shift-drag
  or two fingers pan; wheel or pinch zooms; arrows/+/-/0 work from the keyboard.
  Rendering is matte and never follows the page's light/dark theme.
*/
(function () {
  "use strict";

  var SPIN_SPEED = 0.16;               // radians per second
  var FOV = (35 * Math.PI) / 180;      // vertical field of view
  var START_AZIMUTH = -0.62;           // radians, around the board's Z axis
  var START_ELEVATION = 0.62;          // radians above the board plane
  var MAX_ELEVATION = 1.55;

  var VERTEX_SHADER = [
    "attribute vec3 a_pos;",
    "attribute vec4 a_nc;",
    "uniform mat4 u_viewProj;",
    "uniform mat4 u_model;",
    "uniform vec3 u_qmin;",
    "uniform vec3 u_qsize;",
    "uniform vec3 u_eye;",
    "uniform vec3 u_palette[32];",
    "varying vec3 v_normal;",
    "varying vec3 v_colour;",
    "varying vec3 v_view;",
    "void main() {",
    "  vec4 world = u_model * vec4(u_qmin + a_pos * u_qsize, 1.0);",
    "  v_normal = (u_model * vec4(a_nc.xyz, 0.0)).xyz;",
    "  v_colour = u_palette[int(a_nc.w + 0.5)];",
    "  v_view = u_eye - world.xyz;",
    "  gl_Position = u_viewProj * world;",
    "}"
  ].join("\n");

  var FRAGMENT_SHADER = [
    "precision mediump float;",
    "uniform vec3 u_key;",
    "uniform vec3 u_fill;",
    "varying vec3 v_normal;",
    "varying vec3 v_colour;",
    "varying vec3 v_view;",
    "void main() {",
    "  vec3 n = normalize(v_normal);",
    "  if (dot(n, v_view) < 0.0) n = -n;",          // light both sides of thin parts
    "  float key = max(dot(n, u_key), 0.0);",
    "  float fill = max(dot(n, u_fill), 0.0);",
    "  float sky = 0.5 + 0.5 * n.z;",
    "  vec3 c = v_colour * (0.40 + 0.08 * sky + 0.56 * key + 0.16 * fill);",
    "  gl_FragColor = vec4(min(c, vec3(1.0)), 1.0);",
    "}"
  ].join("\n");

  /* ---------------------------------------------------------------------- */
  /* Small math helpers (column-major 4x4)                                  */
  /* ---------------------------------------------------------------------- */
  function vsub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
  function vcross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
  function vnorm(a) {
    var l = Math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]) || 1;
    return [a[0] / l, a[1] / l, a[2] / l];
  }
  function vcombo(a, ka, b, kb, c, kc) {
    return [a[0] * ka + b[0] * kb + c[0] * kc, a[1] * ka + b[1] * kb + c[1] * kc, a[2] * ka + b[2] * kb + c[2] * kc];
  }
  function perspective(out, fovy, aspect, near, far) {
    var f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    out[0] = f / aspect; out[1] = 0; out[2] = 0; out[3] = 0;
    out[4] = 0; out[5] = f; out[6] = 0; out[7] = 0;
    out[8] = 0; out[9] = 0; out[10] = (far + near) * nf; out[11] = -1;
    out[12] = 0; out[13] = 0; out[14] = 2 * far * near * nf; out[15] = 0;
  }
  function lookAt(out, eye, target, up) {
    var z = vnorm(vsub(eye, target));
    var x = vnorm(vcross(up, z));
    var y = vcross(z, x);
    out[0] = x[0]; out[1] = y[0]; out[2] = z[0]; out[3] = 0;
    out[4] = x[1]; out[5] = y[1]; out[6] = z[1]; out[7] = 0;
    out[8] = x[2]; out[9] = y[2]; out[10] = z[2]; out[11] = 0;
    out[12] = -(x[0] * eye[0] + x[1] * eye[1] + x[2] * eye[2]);
    out[13] = -(y[0] * eye[0] + y[1] * eye[1] + y[2] * eye[2]);
    out[14] = -(z[0] * eye[0] + z[1] * eye[1] + z[2] * eye[2]);
    out[15] = 1;
  }
  function multiply(out, a, b) {
    for (var c = 0; c < 4; c++) {
      for (var r = 0; r < 4; r++) {
        out[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
      }
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Model file                                                             */
  /* ---------------------------------------------------------------------- */
  function parseModel(buffer) {
    var dv = new DataView(buffer);
    var o = 0;
    function u32() { var v = dv.getUint32(o, true); o += 4; return v; }
    function f32() { var v = dv.getFloat32(o, true); o += 4; return v; }
    function align() { o = (o + 3) & ~3; }

    var magic = String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3));
    if (magic !== "PCB3") throw new Error("not a board model");
    o = 4;
    u32(); // version
    var colourCount = u32();
    var palette = new Float32Array(32 * 3);
    for (var i = 0; i < colourCount; i++) {
      if (i < 32) {
        palette[i * 3] = dv.getUint8(o) / 255;
        palette[i * 3 + 1] = dv.getUint8(o + 1) / 255;
        palette[i * 3 + 2] = dv.getUint8(o + 2) / 255;
      }
      o += 4;
    }
    var min = [f32(), f32(), f32()];
    var max = [f32(), f32(), f32()];
    var meshCount = u32();
    var meshes = [];
    for (var m = 0; m < meshCount; m++) {
      var flags = u32();
      var qmin = [f32(), f32(), f32()];
      var qstep = [f32(), f32(), f32()];
      var chunkCount = u32();
      var chunks = [];
      for (var c = 0; c < chunkCount; c++) {
        var vertexCount = u32();
        var indexCount = u32();
        var pos = new Uint16Array(buffer, o, vertexCount * 3);
        o += vertexCount * 6; align();
        var nc = new Int8Array(buffer, o, vertexCount * 4);
        o += vertexCount * 4;
        var idx = new Uint16Array(buffer, o, indexCount);
        o += indexCount * 2; align();
        chunks.push({ pos: pos, nc: nc, idx: idx, count: indexCount });
      }
      meshes.push({
        overlay: (flags & 1) === 1,
        qmin: qmin,
        qsize: [qstep[0] * 65535, qstep[1] * 65535, qstep[2] * 65535],
        chunks: chunks,
        instances: []
      });
    }
    var instanceCount = u32();
    for (var k = 0; k < instanceCount; k++) {
      var meshIndex = u32();
      var r = [];
      for (var j = 0; j < 12; j++) r.push(f32());
      if (!meshes[meshIndex]) continue;
      meshes[meshIndex].instances.push(new Float32Array([
        r[0], r[4], r[8], 0,
        r[1], r[5], r[9], 0,
        r[2], r[6], r[10], 0,
        r[3], r[7], r[11], 1
      ]));
    }
    return { palette: palette, min: min, max: max, meshes: meshes };
  }

  function load(url, onProgress, onDone, onError) {
    var xhr = new XMLHttpRequest();
    xhr.open("GET", url, true);
    xhr.responseType = "arraybuffer";
    xhr.onprogress = function (e) { onProgress(e.loaded, e.lengthComputable ? e.total : 0); };
    xhr.onload = function () {
      if ((xhr.status === 200 || xhr.status === 0) && xhr.response) onDone(xhr.response);
      else onError(new Error("HTTP " + xhr.status));
    };
    xhr.onerror = function () { onError(new Error("network error")); };
    xhr.send();
  }

  /* ---------------------------------------------------------------------- */
  /* Viewer                                                                 */
  /* ---------------------------------------------------------------------- */
  function createViewer(root) {
    var canvas = root.querySelector("canvas");
    var statusEl = root.querySelector("[data-viewer-status]");
    var resetButton = root.querySelector("[data-viewer-reset]");
    var src = root.getAttribute("data-src");
    var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    var gl = null, program = null, loc = {}, model = null, buffer = null;
    var started = false, ready = false, lost = false;
    var spinning = !reduceMotion, engaged = false;
    var target = [0, 0, 0], home = [0, 0, 0], radius = 1;
    var azimuth = START_AZIMUTH, elevation = START_ELEVATION, distance = 1, homeDistance = 1;
    var minDistance = 0.1, maxDistance = 10;
    var viewMatrix = new Float32Array(16), projMatrix = new Float32Array(16), viewProj = new Float32Array(16);
    var raf = 0, lastTime = 0, dirty = true, onScreen = true;
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var slowFrames = 0, frameCount = 0;
    var lastPageScroll = 0;
    var touchFirst = window.matchMedia && window.matchMedia("(hover: none)").matches;
    var HINT = touchFirst ? "Drag to rotate, pinch to zoom" : "Drag to rotate, scroll to zoom";
    var hintShown = false;

    function setStatus(text, hideAfter) {
      if (!statusEl) return;
      statusEl.textContent = text;
      statusEl.hidden = !text;
      if (hideAfter) setTimeout(function () { if (statusEl.textContent === text) statusEl.hidden = true; }, hideAfter);
    }

    /* -- GL setup ----------------------------------------------------------- */
    function compile(type, source) {
      var s = gl.createShader(type);
      gl.shaderSource(s, source);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    }

    function initGL() {
      var opts = { antialias: true, alpha: true, depth: true, premultipliedAlpha: true, preserveDrawingBuffer: false };
      gl = canvas.getContext("webgl", opts) || canvas.getContext("experimental-webgl", opts);
      if (!gl) return false;
      program = gl.createProgram();
      gl.attachShader(program, compile(gl.VERTEX_SHADER, VERTEX_SHADER));
      gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAGMENT_SHADER));
      gl.bindAttribLocation(program, 0, "a_pos");
      gl.bindAttribLocation(program, 1, "a_nc");
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
      var names = ["u_viewProj", "u_model", "u_qmin", "u_qsize", "u_eye", "u_key", "u_fill"];
      for (var i = 0; i < names.length; i++) loc[names[i]] = gl.getUniformLocation(program, names[i]);
      loc.u_palette = gl.getUniformLocation(program, "u_palette[0]");
      gl.enable(gl.DEPTH_TEST);
      gl.disable(gl.CULL_FACE);
      gl.clearColor(0, 0, 0, 0);
      return true;
    }

    function upload() {
      for (var m = 0; m < model.meshes.length; m++) {
        var chunks = model.meshes[m].chunks;
        for (var c = 0; c < chunks.length; c++) {
          var ch = chunks[c];
          ch.pb = gl.createBuffer();
          gl.bindBuffer(gl.ARRAY_BUFFER, ch.pb);
          gl.bufferData(gl.ARRAY_BUFFER, ch.pos, gl.STATIC_DRAW);
          ch.nb = gl.createBuffer();
          gl.bindBuffer(gl.ARRAY_BUFFER, ch.nb);
          gl.bufferData(gl.ARRAY_BUFFER, ch.nc, gl.STATIC_DRAW);
          ch.ib = gl.createBuffer();
          gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ch.ib);
          gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, ch.idx, gl.STATIC_DRAW);
        }
      }
    }

    /* -- Camera ------------------------------------------------------------- */
    function frameModel() {
      var min = model.min, max = model.max;
      home = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
      var dx = max[0] - min[0], dy = max[1] - min[1], dz = max[2] - min[2];
      radius = Math.sqrt(dx * dx + dy * dy + dz * dz) / 2 || 1;
      minDistance = radius * 0.06;
      maxDistance = radius * 5;
      resetView();
    }

    function fitDistance() {
      var aspect = canvas.clientWidth / Math.max(1, canvas.clientHeight) || 1.6;
      var fovX = 2 * Math.atan(Math.tan(FOV / 2) * aspect);
      // wide frames can crop in closer; narrow (phone) frames back off so the long board fits
      var k = 0.7 + 0.25 * Math.max(0, Math.min(1, (1.78 - aspect) / 0.6));
      return (radius / Math.sin(Math.min(FOV, fovX) / 2)) * k;
    }

    function resetView() {
      target = home.slice();
      azimuth = START_AZIMUTH;
      elevation = START_ELEVATION;
      homeDistance = fitDistance();
      distance = homeDistance;
      requestRender();
    }

    function eye() {
      var ce = Math.cos(elevation);
      return [
        target[0] + distance * ce * Math.cos(azimuth),
        target[1] + distance * ce * Math.sin(azimuth),
        target[2] + distance * Math.sin(elevation)
      ];
    }

    function cameraBasis() {
      var e = eye();
      var forward = vnorm(vsub(target, e));
      var right = vnorm(vcross(forward, [0, 0, 1]));
      var up = vcross(right, forward);
      return { eye: e, forward: forward, right: right, up: up };
    }

    function orbit(dx, dy) {
      azimuth -= dx * 0.0075;
      elevation = Math.max(-MAX_ELEVATION, Math.min(MAX_ELEVATION, elevation + dy * 0.0075));
      requestRender();
    }

    function pan(dx, dy) {
      var b = cameraBasis();
      var scale = (2 * distance * Math.tan(FOV / 2)) / Math.max(1, canvas.clientHeight);
      target = vcombo(target, 1, b.right, -dx * scale, b.up, dy * scale);
      // don't let the model wander off completely
      for (var k = 0; k < 3; k++) target[k] = Math.max(home[k] - radius, Math.min(home[k] + radius, target[k]));
      requestRender();
    }

    function zoom(factor) {
      distance = Math.max(minDistance, Math.min(maxDistance, distance * factor));
      requestRender();
    }

    /* -- Drawing ------------------------------------------------------------ */
    function resize() {
      var w = Math.max(1, Math.round(canvas.clientWidth * dpr));
      var h = Math.max(1, Math.round(canvas.clientHeight * dpr));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
        return true;
      }
      return false;
    }

    function draw() {
      var b = cameraBasis();
      var aspect = canvas.width / canvas.height;
      var near = Math.max(distance - radius * 1.3, distance * 0.02);
      var far = distance + radius * 1.3;
      perspective(projMatrix, FOV, aspect, near, far);
      lookAt(viewMatrix, b.eye, target, [0, 0, 1]);
      multiply(viewProj, projMatrix, viewMatrix);

      var key = vnorm(vcombo(b.forward, -1, b.up, 0.75, b.right, -0.5));
      var fill = vnorm(vcombo(b.forward, -0.5, b.up, -0.15, b.right, 0.85));

      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.useProgram(program);
      gl.uniformMatrix4fv(loc.u_viewProj, false, viewProj);
      gl.uniform3fv(loc.u_eye, b.eye);
      gl.uniform3fv(loc.u_key, key);
      gl.uniform3fv(loc.u_fill, fill);
      gl.uniform3fv(loc.u_palette, model.palette);
      gl.enableVertexAttribArray(0);
      gl.enableVertexAttribArray(1);

      for (var m = 0; m < model.meshes.length; m++) {
        var mesh = model.meshes[m];
        if (!mesh.instances.length) continue;
        gl.uniform3fv(loc.u_qmin, mesh.qmin);
        gl.uniform3fv(loc.u_qsize, mesh.qsize);
        if (mesh.overlay) {
          gl.enable(gl.POLYGON_OFFSET_FILL);
          gl.polygonOffset(-1, -4);
        }
        for (var c = 0; c < mesh.chunks.length; c++) {
          var ch = mesh.chunks[c];
          gl.bindBuffer(gl.ARRAY_BUFFER, ch.pb);
          gl.vertexAttribPointer(0, 3, gl.UNSIGNED_SHORT, true, 0, 0);
          gl.bindBuffer(gl.ARRAY_BUFFER, ch.nb);
          gl.vertexAttribPointer(1, 4, gl.BYTE, false, 0, 0);
          gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ch.ib);
          for (var i = 0; i < mesh.instances.length; i++) {
            gl.uniformMatrix4fv(loc.u_model, false, mesh.instances[i]);
            gl.drawElements(gl.TRIANGLES, ch.count, gl.UNSIGNED_SHORT, 0);
          }
        }
        if (mesh.overlay) gl.disable(gl.POLYGON_OFFSET_FILL);
      }
    }

    function frame(time) {
      raf = 0;
      if (!ready || lost || !onScreen || document.hidden) { lastTime = 0; return; }
      var dt = lastTime ? Math.min(0.1, (time - lastTime) / 1000) : 0;
      lastTime = time;
      if (spinning) { azimuth -= SPIN_SPEED * dt; dirty = true; }
      if (resize()) dirty = true;
      if (dirty) {
        var t0 = Date.now();
        draw();
        dirty = false;
        if (!root.className.match(/\bis-ready\b/)) root.className += " is-ready";
        if (!hintShown) { hintShown = true; if (!engaged) setStatus(HINT, 5000); }
        adaptResolution(Date.now() - t0, dt);
      }
      if (spinning) raf = requestAnimationFrame(frame);
      else lastTime = 0;
    }

    // If frames are slow while spinning, render at a lower resolution.
    function adaptResolution(drawMs, dt) {
      if (!spinning || dpr <= 1 || !dt) return;
      frameCount++;
      if (dt > 0.045) slowFrames++;
      if (frameCount >= 40) {
        if (slowFrames > 24) { dpr = Math.max(1, dpr * 0.75); dirty = true; }
        frameCount = 0;
        slowFrames = 0;
      }
    }

    function requestRender() {
      dirty = true;
      if (!raf && ready) raf = requestAnimationFrame(frame);
    }

    /* -- Interaction -------------------------------------------------------- */
    function engage() {
      if (!ready) return;
      spinning = false;
      if (statusEl && statusEl.textContent === HINT) statusEl.hidden = true;
      if (!engaged) {
        engaged = true;
        root.className += " is-engaged";
        if (resetButton) resetButton.hidden = false;
      }
    }
    function disengage() {
      if (!engaged) return;
      engaged = false;
      root.className = root.className.replace(/\s*\bis-engaged\b/g, "");
    }

    var passive = false;
    try {
      var opts = Object.defineProperty({}, "passive", { get: function () { passive = { passive: false }; return true; } });
      window.addEventListener("test", null, opts);
      window.removeEventListener("test", null, opts);
    } catch (err) { /* old browser */ }

    function bindInput() {
      canvas.addEventListener("contextmenu", function (e) { e.preventDefault(); });
      window.addEventListener("scroll", function () { lastPageScroll = Date.now(); }, passive ? { passive: true } : false);

      var onWheel = function (e) {
        if (!ready) return;
        // keep page scrolling smooth when the cursor merely passes over the viewer
        if (!engaged && Date.now() - lastPageScroll < 300) return;
        var dy = e.deltaY !== undefined ? e.deltaY : -(e.wheelDelta || 0);
        if (e.deltaMode === 1) dy *= 16;
        else if (e.deltaMode === 2) dy *= 400;
        engage();
        zoom(Math.exp(Math.max(-200, Math.min(200, dy)) * 0.0015));
        e.preventDefault();
      };
      canvas.addEventListener("wheel", onWheel, passive);
      if (!("onwheel" in canvas)) canvas.addEventListener("mousewheel", onWheel, passive);

      if (window.PointerEvent) bindPointer();
      else { bindMouse(); bindTouch(); }

      canvas.addEventListener("keydown", function (e) {
        if (!ready) return;
        var k = e.key || e.keyCode, step = 24, handled = true;
        if (k === "ArrowLeft" || k === "Left" || k === 37) { if (e.shiftKey) pan(step, 0); else orbit(step, 0); }
        else if (k === "ArrowRight" || k === "Right" || k === 39) { if (e.shiftKey) pan(-step, 0); else orbit(-step, 0); }
        else if (k === "ArrowUp" || k === "Up" || k === 38) { if (e.shiftKey) pan(0, step); else orbit(0, step); }
        else if (k === "ArrowDown" || k === "Down" || k === 40) { if (e.shiftKey) pan(0, -step); else orbit(0, -step); }
        else if (k === "+" || k === "=" || k === 187 || k === 107) zoom(0.85);
        else if (k === "-" || k === "_" || k === 189 || k === 109) zoom(1 / 0.85);
        else if (k === "0" || k === "Home" || k === 48 || k === 36) { reset(); return e.preventDefault(); }
        else handled = false;
        if (handled) { engage(); e.preventDefault(); }
      });

      // clicking anywhere else on the page hands touch scrolling back to the page
      document.addEventListener(window.PointerEvent ? "pointerdown" : "mousedown", function (e) {
        if (!root.contains(e.target)) disengage();
      }, true);

      if (resetButton) resetButton.addEventListener("click", reset);
    }

    function reset() {
      disengage();
      spinning = !reduceMotion;
      if (resetButton) resetButton.hidden = true;
      resetView();
    }

    function bindPointer() {
      var pointers = [];
      function find(id) { for (var i = 0; i < pointers.length; i++) if (pointers[i].id === id) return pointers[i]; return null; }
      canvas.addEventListener("pointerdown", function (e) {
        if (!ready) return;
        // a mouse click is intent; a finger might just be scrolling the page
        if (e.pointerType !== "touch") engage();
        try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
        pointers.push({ id: e.pointerId, x: e.clientX, y: e.clientY, pan: e.button === 1 || e.button === 2 || e.shiftKey || e.ctrlKey || e.metaKey });
        if (e.pointerType === "mouse") e.preventDefault();
      });
      canvas.addEventListener("pointermove", function (e) {
        var p = find(e.pointerId);
        if (!p) return;
        engage(); // touches only reach us here if the page isn't scrolling instead
        if (pointers.length === 1) {
          if (p.pan) pan(e.clientX - p.x, e.clientY - p.y);
          else orbit(e.clientX - p.x, e.clientY - p.y);
        } else if (pointers.length >= 2) {
          var q = pointers[0] === p ? pointers[1] : pointers[0];
          twoFinger(p.x, p.y, e.clientX, e.clientY, q.x, q.y);
        }
        p.x = e.clientX;
        p.y = e.clientY;
      });
      function release(e) {
        for (var i = 0; i < pointers.length; i++) if (pointers[i].id === e.pointerId) { pointers.splice(i, 1); break; }
      }
      canvas.addEventListener("pointerup", release);
      canvas.addEventListener("pointercancel", release);
    }

    // One finger moved from (ax,ay) to (bx,by) while the other stays at (cx,cy)
    function twoFinger(ax, ay, bx, by, cx, cy) {
      var before = Math.sqrt((ax - cx) * (ax - cx) + (ay - cy) * (ay - cy)) || 1;
      var after = Math.sqrt((bx - cx) * (bx - cx) + (by - cy) * (by - cy)) || 1;
      zoom(before / after);
      pan((bx - ax) / 2, (by - ay) / 2);
    }

    function bindMouse() {
      var down = null;
      canvas.addEventListener("mousedown", function (e) {
        if (!ready) return;
        engage();
        down = { x: e.clientX, y: e.clientY, pan: e.button === 1 || e.button === 2 || e.shiftKey || e.ctrlKey || e.metaKey };
        e.preventDefault();
      });
      document.addEventListener("mousemove", function (e) {
        if (!down) return;
        if (down.pan) pan(e.clientX - down.x, e.clientY - down.y);
        else orbit(e.clientX - down.x, e.clientY - down.y);
        down.x = e.clientX;
        down.y = e.clientY;
      });
      document.addEventListener("mouseup", function () { down = null; });
    }

    function bindTouch() {
      var last = null, decided = false, mine = false;
      function snapshot(t) {
        var s = [];
        for (var i = 0; i < t.length && i < 2; i++) s.push({ x: t[i].clientX, y: t[i].clientY });
        return s;
      }
      canvas.addEventListener("touchstart", function (e) {
        if (!ready) return;
        last = snapshot(e.touches);
        decided = e.touches.length > 1 || engaged;
        mine = decided;
        if (mine) { engage(); e.preventDefault(); }
      }, passive);
      canvas.addEventListener("touchmove", function (e) {
        if (!last) return;
        var now = snapshot(e.touches);
        if (!decided && now.length === 1) {
          // before the viewer is engaged, vertical swipes keep scrolling the page
          var dx = Math.abs(now[0].x - last[0].x), dy = Math.abs(now[0].y - last[0].y);
          if (dx + dy < 6) return;
          decided = true;
          mine = dx > dy;
          if (mine) engage();
        }
        if (!mine && now.length < 2) return;
        if (now.length >= 2 && last.length >= 2) {
          var s0 = Math.sqrt(Math.pow(last[0].x - last[1].x, 2) + Math.pow(last[0].y - last[1].y, 2)) || 1;
          var s1 = Math.sqrt(Math.pow(now[0].x - now[1].x, 2) + Math.pow(now[0].y - now[1].y, 2)) || 1;
          zoom(s0 / s1);
          pan((now[0].x + now[1].x - last[0].x - last[1].x) / 2, (now[0].y + now[1].y - last[0].y - last[1].y) / 2);
          mine = true;
          engage();
        } else if (now.length === 1 && last.length === 1) {
          orbit(now[0].x - last[0].x, now[0].y - last[0].y);
        }
        last = now;
        e.preventDefault();
      }, passive);
      canvas.addEventListener("touchend", function (e) {
        last = e.touches.length ? snapshot(e.touches) : null;
      });
    }

    /* -- Start -------------------------------------------------------------- */
    function start() {
      if (started) return;
      started = true;
      try {
        if (!initGL()) {
          setStatus("3D preview isn’t available in this browser, so this is a still image.");
          return;
        }
      } catch (err) {
        setStatus("3D preview isn’t available in this browser, so this is a still image.");
        return;
      }
      setStatus("Loading 3D model…");
      load(src, function (loaded, total) {
        if (total) setStatus("Loading 3D model… " + Math.round((loaded / total) * 100) + "%");
        else setStatus("Loading 3D model… " + (loaded / 1048576).toFixed(1) + " MB");
      }, function (data) {
        try {
          buffer = data;
          model = parseModel(buffer);
          upload();
        } catch (err) {
          setStatus("Couldn’t open the 3D model. Reload the page to try again.");
          return;
        }
        ready = true;
        frameModel();
        bindInput();
        setStatus("");
        requestRender();
      }, function () {
        setStatus("Couldn’t load the 3D model. Reload the page to try again.");
      });
    }

    canvas.addEventListener("webglcontextlost", function (e) {
      e.preventDefault();
      lost = true;
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
    }, false);
    canvas.addEventListener("webglcontextrestored", function () {
      lost = false;
      try { initGL(); upload(); requestRender(); } catch (err) { setStatus("3D preview stopped. Reload the page to restart it."); }
    }, false);

    if (window.addEventListener) {
      window.addEventListener("resize", requestRender);
      window.addEventListener("orientationchange", requestRender);
      document.addEventListener("visibilitychange", function () { if (!document.hidden) requestRender(); });
    }
    if (window.ResizeObserver) new window.ResizeObserver(function () { requestRender(); }).observe(canvas);

    // Load when the viewer gets near the screen; pause drawing while it's off screen.
    if (window.IntersectionObserver) {
      new window.IntersectionObserver(function (entries) {
        for (var i = 0; i < entries.length; i++) {
          if (entries[i].isIntersecting) start();
          onScreen = entries[i].isIntersecting;
          if (onScreen) requestRender();
        }
      }, { rootMargin: "400px 0px" }).observe(root);
    } else {
      start();
    }
  }

  var roots = document.querySelectorAll("[data-board-viewer]");
  for (var i = 0; i < roots.length; i++) createViewer(roots[i]);
})();
