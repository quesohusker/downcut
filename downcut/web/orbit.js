'use strict';

window.orbit = (() => {
  const view = { canvas: null, gl: null, program: null, buffers: {}, count: 0, yaw: -0.6, pitch: 0.75, zoom: 1.5, span: 1, drag: null };

  const VERTEX = `#version 300 es
in vec3 position;
in vec3 normal;
uniform mat4 matrix;
out vec3 vNormal;
out float vDepth;
uniform float thickness;
void main() {
  vNormal = normal;
  vDepth = clamp(-position.z / thickness, 0.0, 1.0);
  gl_Position = matrix * vec4(position, 1.0);
}`;

  const FRAGMENT = `#version 300 es
precision mediump float;
in vec3 vNormal;
in float vDepth;
out vec4 color;
void main() {
  vec3 light = normalize(vec3(-0.45, 0.55, 0.7));
  float shade = max(dot(normalize(vNormal), light), 0.0);
  vec3 top = vec3(0.84, 0.69, 0.5);
  vec3 deep = vec3(0.49, 0.35, 0.21);
  vec3 tint = mix(top, deep, vDepth);
  if (vDepth > 0.995) tint = vec3(0.13, 0.14, 0.16);
  color = vec4(tint * (0.32 + 0.9 * shade), 1.0);
}`;

  function compile(gl, type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    return shader;
  }

  function perspective(fov, aspect, near, far) {
    const f = 1 / Math.tan(fov / 2);
    return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) / (near - far), -1, 0, 0, (2 * far * near) / (near - far), 0];
  }

  function multiply(a, b) {
    const out = new Array(16).fill(0);
    for (let c = 0; c < 4; c++) {
      for (let r = 0; r < 4; r++) {
        for (let k = 0; k < 4; k++) out[c * 4 + r] += a[k * 4 + r] * b[c * 4 + k];
      }
    }
    return out;
  }

  function camera(aspect) {
    const distance = view.span * view.zoom * Math.max(1, 1 / aspect);
    const cy = Math.cos(view.yaw), sy = Math.sin(view.yaw);
    const cp = Math.cos(view.pitch), sp = Math.sin(view.pitch);
    const eye = [distance * cp * sy, -distance * cp * cy, distance * sp];
    const forward = eye.map((v) => -v / distance);
    const up = [0, 0, 1];
    const side = [forward[1] * up[2] - forward[2] * up[1], forward[2] * up[0] - forward[0] * up[2], forward[0] * up[1] - forward[1] * up[0]];
    const length = Math.hypot(...side) || 1;
    const s = side.map((v) => v / length);
    const u = [s[1] * forward[2] - s[2] * forward[1], s[2] * forward[0] - s[0] * forward[2], s[0] * forward[1] - s[1] * forward[0]];
    return [
      s[0], u[0], -forward[0], 0,
      s[1], u[1], -forward[1], 0,
      s[2], u[2], -forward[2], 0,
      -(s[0] * eye[0] + s[1] * eye[1] + s[2] * eye[2]),
      -(u[0] * eye[0] + u[1] * eye[1] + u[2] * eye[2]),
      forward[0] * eye[0] + forward[1] * eye[1] + forward[2] * eye[2],
      1,
    ];
  }

  function paint() {
    const { gl, canvas } = view;
    if (!gl || !view.count) return;
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(canvas.clientWidth * ratio));
    const height = Math.max(1, Math.round(canvas.clientHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    gl.viewport(0, 0, width, height);
    gl.clearColor(0.11, 0.12, 0.13, 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    const aspect = width / height;
    const matrix = multiply(perspective(0.7, aspect, view.span * 0.02, view.span * 12 * Math.max(1, 1 / aspect)), camera(aspect));
    gl.uniformMatrix4fv(gl.getUniformLocation(view.program, 'matrix'), false, new Float32Array(matrix));
    gl.drawElements(gl.TRIANGLES, view.count, gl.UNSIGNED_INT, 0);
  }

  function bind(canvas) {
    canvas.addEventListener('pointerdown', (event) => {
      canvas.setPointerCapture(event.pointerId);
      view.drag = { x: event.clientX, y: event.clientY, yaw: view.yaw, pitch: view.pitch };
    });
    canvas.addEventListener('pointermove', (event) => {
      if (!view.drag) return;
      view.yaw = view.drag.yaw - (event.clientX - view.drag.x) * 0.008;
      view.pitch = Math.max(0.05, Math.min(1.55, view.drag.pitch + (event.clientY - view.drag.y) * 0.008));
      paint();
    });
    canvas.addEventListener('pointerup', () => { view.drag = null; });
    canvas.addEventListener('wheel', (event) => {
      event.preventDefault();
      view.zoom = Math.max(0.35, Math.min(5, view.zoom * Math.exp(event.deltaY * 0.002)));
      paint();
    }, { passive: false });
    canvas.dataset.bound = '1';
  }

  function load(canvas, surface, thickness, floor = null) {
    view.canvas = canvas;
    if (!canvas.dataset.bound) bind(canvas);
    const gl = view.gl || canvas.getContext('webgl2', { antialias: true });
    if (!gl) return false;
    view.gl = gl;
    if (!view.program) {
      const program = gl.createProgram();
      gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX));
      gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT));
      gl.linkProgram(program);
      gl.useProgram(program);
      view.program = program;
      gl.enable(gl.DEPTH_TEST);
    }
    const bytes = Uint8Array.from(atob(surface.heights), (c) => c.charCodeAt(0));
    const heights = new Float32Array(bytes.buffer);
    if (floor !== null) {
      for (let at = 0; at < heights.length; at++) heights[at] = Math.max(heights[at], floor);
    }
    const { nx, ny, step } = surface;
    const positions = new Float32Array(nx * ny * 3);
    const normals = new Float32Array(nx * ny * 3);
    const cx = ((nx - 1) * step) / 2, cy = ((ny - 1) * step) / 2;
    for (let j = 0; j < ny; j++) {
      for (let i = 0; i < nx; i++) {
        const at = j * nx + i;
        positions[at * 3] = i * step - cx;
        positions[at * 3 + 1] = j * step - cy;
        positions[at * 3 + 2] = heights[at];
        const left = heights[j * nx + Math.max(i - 1, 0)], right = heights[j * nx + Math.min(i + 1, nx - 1)];
        const down = heights[Math.max(j - 1, 0) * nx + i], up = heights[Math.min(j + 1, ny - 1) * nx + i];
        const dx = (right - left) / (2 * step), dy = (up - down) / (2 * step);
        const length = Math.hypot(dx, dy, 1);
        normals[at * 3] = -dx / length;
        normals[at * 3 + 1] = -dy / length;
        normals[at * 3 + 2] = 1 / length;
      }
    }
    const indices = new Uint32Array((nx - 1) * (ny - 1) * 6);
    let cursor = 0;
    for (let j = 0; j < ny - 1; j++) {
      for (let i = 0; i < nx - 1; i++) {
        const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
        indices.set([a, b, d, a, d, c], cursor);
        cursor += 6;
      }
    }
    const attribute = (name, data) => {
      view.buffers[name] = view.buffers[name] || gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, view.buffers[name]);
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      const where = gl.getAttribLocation(view.program, name);
      gl.enableVertexAttribArray(where);
      gl.vertexAttribPointer(where, 3, gl.FLOAT, false, 0, 0);
    };
    attribute('position', positions);
    attribute('normal', normals);
    view.buffers.index = view.buffers.index || gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, view.buffers.index);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
    gl.uniform1f(gl.getUniformLocation(view.program, 'thickness'), Math.max(thickness, 0.001));
    view.count = indices.length;
    view.span = Math.max(nx, ny) * step;
    paint();
    return true;
  }

  return { load, paint };
})();
