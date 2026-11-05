'use strict';

const MM = 25.4;
const FILE_VERSION = 1;
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  boot: null,
  job: null,
  selection: new Set(),
  tool: 'select',
  view: { scale: 1, ox: 0, oy: 0 },
  computed: null,
  history: [],
  future: [],
  draft: null,
  activeOp: null,
  gesture: null,
  spaceDown: false,
  computeToken: 0,
  computeTimer: null,
  bounds: new Map(),
  cutPath: null,
  rapidPath: null,
  activeLayer: 'Layer 1',
  snap: { grid: false, smart: true, step: MM / 4 },
  rulers: true,
  pen: null,
  pointer: null,
  snapMark: null,
  nodeId: null,
  nodeIndex: -1,
  measure: null,
  refId: null,
  issueMark: null,
  checked: new Set(),
  filter: { material: '', machine: '' },
  library: { posts: [], tools: [], machines: [] },
  toolEdit: null,
  postEdit: null,
  sheet: null,
  surface: null,
  surfaceStale: true,
  previewToken: 0,
  form: null,
  viewMode: '2d',
  previewMode: 'orbit',
};

const canvas = $('#canvas');
const ctx = canvas.getContext('2d');

async function call(path, body) {
  const options = body === undefined
    ? {}
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.detail || 'The engine could not finish that step.');
  return data;
}

function newId() {
  return Math.random().toString(16).slice(2, 12);
}

function say(message, isError = false) {
  const node = $('#status-msg');
  node.textContent = message;
  node.classList.toggle('error', isError);
}

const inches = () => state.job.units === 'in';
const toUnits = (mm) => (inches() ? mm / MM : mm);
const toMm = (value) => (inches() ? value * MM : value);
const trim = (value) => String(Number(value.toFixed(inches() ? 4 : 2)));
const unitLabel = () => (inches() ? 'in' : 'mm');

function newJob() {
  return {
    material: { width: 24 * MM, height: 18 * MM, thickness: 0.75 * MM },
    units: 'in',
    z_zero: 'top',
    xy_origin: 'lower_left',
    safe_z: 0.2 * MM,
    spoilboard_allowance: 0.02 * MM,
    vectors: [],
    tools: state.boot.tools.map((tool) => ({ ...tool })),
    operations: [],
    layers: [{ name: 'Layer 1', visible: true, locked: false, keepout: false }],
    guides: [],
    dimensions: [],
    texts: {},
    custom_posts: [],
    arc_output: false,
    tiling: {},
    machine: {},
  };
}

function normalizeJob(job) {
  if (!job.layers || !job.layers.length) job.layers = [{ name: 'Layer 1', visible: true, locked: false, keepout: false }];
  job.guides = job.guides || [];
  job.dimensions = job.dimensions || [];
  job.texts = job.texts || {};
  job.custom_posts = job.custom_posts || [];
  job.arc_output = Boolean(job.arc_output);
  job.tiling = job.tiling || {};
  job.machine = job.machine || {};
  const known = new Set(job.tools.map((tool) => tool.id));
  for (const tool of [...state.boot.tools, ...state.library.tools]) {
    if (!known.has(tool.id)) {
      job.tools.push({ ...tool });
      known.add(tool.id);
    }
  }
  for (const op of job.operations) op.enabled = op.enabled !== false;
  return job;
}

function refreshBounds() {
  state.bounds.clear();
  for (const vector of state.job.vectors) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [x, y] of vector.points) {
      if (x < x0) x0 = x;
      if (y < y0) y0 = y;
      if (x > x1) x1 = x;
      if (y > y1) y1 = y;
    }
    state.bounds.set(vector.id, [x0, y0, x1, y1]);
  }
}

function snapshot() {
  const { vectors, operations, layers, guides, dimensions, texts } = state.job;
  return JSON.stringify({ vectors, operations, layers, guides, dimensions, texts });
}

function remember() {
  state.history.push(snapshot());
  if (state.history.length > 80) state.history.shift();
  state.future = [];
}

function restore(text) {
  const data = JSON.parse(text);
  Object.assign(state.job, data);
  if (!layerOf(state.activeLayer)) state.activeLayer = state.job.layers[0].name;
  const known = new Set(state.job.vectors.map((v) => v.id));
  state.selection = new Set([...state.selection].filter((id) => known.has(id)));
  state.draft = null;
  state.activeOp = null;
  renderDimensions();
  changed();
}

function undo() {
  if (!state.history.length) return;
  state.future.push(snapshot());
  restore(state.history.pop());
}

function redo() {
  if (!state.future.length) return;
  state.history.push(snapshot());
  restore(state.future.pop());
}

function changed() {
  state.surfaceStale = true;
  refreshBounds();
  renderJobStatus();
  renderDesign();
  renderLayers();
  renderOperations();
  draw();
  platform.writeDraft(JSON.stringify({ format: 'downcut', version: FILE_VERSION, job: state.job }));
  scheduleCompute();
}

function scheduleCompute() {
  clearTimeout(state.computeTimer);
  state.computeTimer = setTimeout(compute, 250);
}

async function compute() {
  const token = ++state.computeToken;
  if (!state.job.operations.some((op) => op.enabled !== false)) {
    state.computed = null;
    state.cutPath = state.rapidPath = null;
    renderOperations();
    draw();
    return;
  }
  say('Calculating toolpaths');
  try {
    const result = await call('/api/compute', { job: state.job, post: $('#post-select').value, title: $('#job-title').value });
    if (token !== state.computeToken) return;
    state.computed = result;
    buildPathShapes();
    renderOperations();
    draw();
    say(result.notices.join(' ') || 'Toolpaths are up to date', result.notices.length > 0);
    if (state.viewMode === '3d') runPreview();
  } catch (error) {
    if (token === state.computeToken) say(error.message, true);
  }
}

function buildPathShapes() {
  const cut = new Path2D();
  const rapid = new Path2D();
  for (const path of state.computed.paths) {
    for (const run of path.runs) {
      const target = run.kind === 'cut' ? cut : rapid;
      run.points.forEach(([x, y], index) => (index ? target.lineTo(x, y) : target.moveTo(x, y)));
    }
  }
  state.cutPath = cut;
  state.rapidPath = rapid;
}

function resize() {
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(canvas.clientWidth * ratio);
  canvas.height = Math.round(canvas.clientHeight * ratio);
  draw();
}

function fit() {
  const { width, height } = state.job.material;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const scale = Math.min((w - 90) / width, (h - 90) / height);
  state.view = { scale, ox: (w - width * scale) / 2, oy: (h + height * scale) / 2 };
  draw();
}

const screenX = (x) => state.view.ox + x * state.view.scale;
const screenY = (y) => state.view.oy - y * state.view.scale;
const worldX = (x) => (x - state.view.ox) / state.view.scale;
const worldY = (y) => (state.view.oy - y) / state.view.scale;

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function selectionBounds() {
  let box = null;
  for (const id of state.selection) {
    const b = state.bounds.get(id);
    if (!b) continue;
    box = box
      ? [Math.min(box[0], b[0]), Math.min(box[1], b[1]), Math.max(box[2], b[2]), Math.max(box[3], b[3])]
      : [...b];
  }
  return box;
}

function handles(box) {
  return [[box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]]];
}

function draw() {
  if (!state.job || state.viewMode !== '2d') return;
  const ratio = window.devicePixelRatio || 1;
  const { scale, ox, oy } = state.view;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.setTransform(ratio * scale, 0, 0, -ratio * scale, ratio * ox, ratio * oy);

  const { width, height } = state.job.material;
  const dark = matchMedia('(prefers-color-scheme: dark)').matches;
  ctx.fillStyle = dark ? '#34302a' : '#f6efe2';
  ctx.fillRect(0, 0, width, height);

  const step = inches() ? MM : 10;
  const major = inches() ? 6 : 10;
  if (step * scale > 6) {
    ctx.lineWidth = 1 / scale;
    for (let i = 1; i * step < width; i++) {
      ctx.strokeStyle = i % major ? (dark ? '#413c34' : '#e8dfcf') : (dark ? '#544d43' : '#d4c8b3');
      ctx.beginPath();
      ctx.moveTo(i * step, 0);
      ctx.lineTo(i * step, height);
      ctx.stroke();
    }
    for (let j = 1; j * step < height; j++) {
      ctx.strokeStyle = j % major ? (dark ? '#413c34' : '#e8dfcf') : (dark ? '#544d43' : '#d4c8b3');
      ctx.beginPath();
      ctx.moveTo(0, j * step);
      ctx.lineTo(width, j * step);
      ctx.stroke();
    }
  }
  ctx.strokeStyle = css('--muted');
  ctx.lineWidth = 1.5 / scale;
  ctx.strokeRect(0, 0, width, height);

  drawGuides();

  if ($('#show-paths').checked && state.cutPath) {
    ctx.strokeStyle = dark ? '#6fcf97' : '#2c7a4b';
    ctx.lineWidth = 1 / scale;
    ctx.stroke(state.cutPath);
  }
  if ($('#show-rapids').checked && state.rapidPath) {
    ctx.strokeStyle = dark ? '#ff8a80' : '#c0392b';
    ctx.lineWidth = 0.6 / scale;
    ctx.stroke(state.rapidPath);
  }

  for (const vector of state.job.vectors) {
    if (!shownVector(vector)) continue;
    const selected = state.selection.has(vector.id);
    const keepout = layerOf(vector.layer)?.keepout;
    ctx.strokeStyle = selected ? css('--accent') : keepout ? '#c0392b' : (dark ? '#d8d4cb' : '#2f2b26');
    ctx.lineWidth = (selected ? 1.6 : 1.2) / scale;
    ctx.setLineDash(selected ? [6 / scale, 4 / scale] : []);
    ctx.beginPath();
    vector.points.forEach(([x, y], index) => (index ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    if (vector.closed) ctx.closePath();
    if (keepout && vector.closed) {
      ctx.fillStyle = 'rgba(192, 57, 43, 0.18)';
      ctx.fill();
    }
    ctx.stroke();
  }
  ctx.setLineDash([]);

  const box = selectionBounds();
  if (box && state.tool === 'select') {
    ctx.strokeStyle = css('--accent');
    ctx.lineWidth = 1 / scale;
    ctx.setLineDash([5 / scale, 4 / scale]);
    ctx.strokeRect(box[0], box[1], box[2] - box[0], box[3] - box[1]);
    ctx.setLineDash([]);
    ctx.fillStyle = css('--accent');
    const size = 8 / scale;
    for (const [x, y] of handles(box)) ctx.fillRect(x - size / 2, y - size / 2, size, size);
  }

  const gesture = state.gesture;
  if (gesture && (gesture.type === 'marquee' || gesture.type === 'shape')) {
    ctx.strokeStyle = css('--accent');
    ctx.lineWidth = 1 / scale;
    ctx.setLineDash([4 / scale, 3 / scale]);
    const [x0, y0] = gesture.start;
    const [x1, y1] = gesture.now;
    if (gesture.type === 'marquee' || state.tool === 'rectangle') {
      ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
    } else if (state.tool === 'ellipse') {
      ctx.beginPath();
      ctx.ellipse(x0, y0, Math.abs(x1 - x0), Math.abs(y1 - y0), 0, 0, Math.PI * 2);
      ctx.stroke();
    } else {
      ctx.beginPath();
      ctx.arc(x0, y0, Math.hypot(x1 - x0, y1 - y0), 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.setLineDash([]);
  }
  if (state.tool === 'node') drawNodes();
  drawPen();
  drawDimensions(ratio);
  drawRulers(ratio);
}

function distanceToSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const length = dx * dx + dy * dy;
  const t = length ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / length)) : 0;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function insidePolygon(px, py, points) {
  let inside = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const [xi, yi] = points[i], [xj, yj] = points[j];
    if ((yi > py) !== (yj > py) && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function pick(x, y) {
  const reach = 7 / state.view.scale;
  let best = null, bestDistance = reach;
  for (const vector of state.job.vectors) {
    if (!pickableVector(vector)) continue;
    const b = state.bounds.get(vector.id);
    if (x < b[0] - reach || x > b[2] + reach || y < b[1] - reach || y > b[3] + reach) continue;
    const pts = vector.points;
    for (let i = 0; i < pts.length - 1 + (vector.closed ? 1 : 0); i++) {
      const a = pts[i], c = pts[(i + 1) % pts.length];
      const d = distanceToSegment(x, y, a[0], a[1], c[0], c[1]);
      if (d < bestDistance) {
        bestDistance = d;
        best = vector;
      }
    }
  }
  if (best) return best;
  let smallest = null, smallestArea = Infinity;
  for (const vector of state.job.vectors) {
    const b = state.bounds.get(vector.id);
    if (!vector.closed || !pickableVector(vector) || x < b[0] || x > b[2] || y < b[1] || y > b[3]) continue;
    const area = (b[2] - b[0]) * (b[3] - b[1]);
    if (area < smallestArea && insidePolygon(x, y, vector.points)) {
      smallest = vector;
      smallestArea = area;
    }
  }
  return smallest;
}

function withGroup(vector) {
  if (!vector.group) return [vector];
  return state.job.vectors.filter((other) => other.group === vector.group);
}

function pointerPosition(event) {
  const rect = canvas.getBoundingClientRect();
  return [event.clientX - rect.left, event.clientY - rect.top];
}

function originals() {
  const map = new Map();
  for (const vector of state.job.vectors) {
    if (state.selection.has(vector.id)) {
      map.set(vector.id, {
        points: vector.points.map((p) => [...p]),
        anchors: vector.anchors ? vector.anchors.map((p) => [...p]) : null,
      });
    }
  }
  return map;
}

function onPointerDown(event) {
  canvas.setPointerCapture(event.pointerId);
  const [sx, sy] = pointerPosition(event);
  const world = [worldX(sx), worldY(sy)];
  if (event.button === 1 || state.spaceDown) {
    state.gesture = { type: 'pan', sx, sy, ox: state.view.ox, oy: state.view.oy };
    return;
  }
  if (rulerDown(sx, sy)) return;
  if (state.tool === 'text') {
    openForm('text');
    $('#text-value').focus();
    return;
  }
  if (PEN_TOOLS.has(state.tool)) {
    if (event.detail >= 2 && state.pen && state.pen.points.length > 1) finishPen(false);
    else penClick(snapped(world));
    return;
  }
  if (state.tool === 'node') {
    nodeDown(world, event);
    return;
  }
  if (state.tool === 'trim') {
    trimClick(world);
    return;
  }
  if (state.tool === 'extend') {
    extendClick(world);
    return;
  }
  if (state.tool === 'measure') {
    measureClick(snapped(world));
    return;
  }
  if (state.tool !== 'select') {
    const start = snapped(world);
    state.gesture = { type: 'shape', start, now: start };
    return;
  }
  const box = selectionBounds();
  if (box) {
    const corners = handles(box);
    const reach = 9 / state.view.scale;
    for (let i = 0; i < 4; i++) {
      if (Math.hypot(world[0] - corners[i][0], world[1] - corners[i][1]) < reach) {
        state.gesture = {
          type: 'scale',
          anchor: corners[(i + 2) % 4],
          corner: corners[i],
          start: originals(),
          moved: false,
        };
        return;
      }
    }
  }
  const hit = pick(world[0], world[1]);
  if (hit) {
    const members = withGroup(hit).map((v) => v.id);
    if (event.shiftKey) {
      const add = !state.selection.has(hit.id);
      members.forEach((id) => (add ? state.selection.add(id) : state.selection.delete(id)));
    } else if (!state.selection.has(hit.id)) {
      state.selection = new Set(members);
    }
    state.gesture = { type: 'move', start: world, base: originals(), box: selectionBounds(), moved: false };
    renderDesign();
    renderForm();
    draw();
    return;
  }
  const guide = guideAt(sx, sy);
  if (guide) {
    remember();
    state.gesture = { type: 'guide', guide, sx, sy };
    return;
  }
  if (!event.shiftKey) state.selection = new Set();
  state.gesture = { type: 'marquee', start: world, now: world };
  renderDesign();
  renderForm();
  draw();
}

function applyPoints(map, transform) {
  for (const vector of state.job.vectors) {
    const base = map.get(vector.id);
    if (base) {
      vector.points = base.points.map(transform);
      if (base.anchors) vector.anchors = base.anchors.map(transform);
    }
  }
  refreshBounds();
}

function onPointerMove(event) {
  const [sx, sy] = pointerPosition(event);
  const world = [worldX(sx), worldY(sy)];
  $('#status-pos').textContent = `X ${trim(toUnits(world[0]))}  Y ${trim(toUnits(world[1]))} ${unitLabel()}`;
  const gesture = state.gesture;
  if (PEN_TOOLS.has(state.tool) || state.tool === 'measure') {
    state.pointer = snapped(world);
    draw();
  }
  if (!gesture) return;
  if (gesture.type === 'guide') {
    guideDrag(gesture, sx, sy, world);
  } else if (gesture.type === 'node') {
    const vector = nodeVector();
    if (vector && state.nodeIndex >= 0) {
      if (!gesture.moved) remember();
      gesture.moved = true;
      vectorNodes(vector)[state.nodeIndex] = snapped(world, new Set([vector.id]));
      if (vector.anchors) vector.points = vector.anchors.map((point) => [...point]);
      refreshBounds();
    }
  } else if (gesture.type === 'pan') {
    state.view.ox = gesture.ox + sx - gesture.sx;
    state.view.oy = gesture.oy + sy - gesture.sy;
  } else if (gesture.type === 'move') {
    const dx = world[0] - gesture.start[0], dy = world[1] - gesture.start[1];
    if (Math.hypot(dx, dy) * state.view.scale > 2) gesture.moved = true;
    if (gesture.moved) {
      const [sdx, sdy] = snapDelta(gesture.box, dx, dy, state.selection);
      applyPoints(gesture.base, ([x, y]) => [x + sdx, y + sdy]);
    }
  } else if (gesture.type === 'scale') {
    const [ax, ay] = gesture.anchor;
    const full = Math.hypot(gesture.corner[0] - ax, gesture.corner[1] - ay);
    const factor = Math.max(0.01, Math.hypot(world[0] - ax, world[1] - ay) / full);
    gesture.moved = true;
    applyPoints(gesture.start, ([x, y]) => [ax + (x - ax) * factor, ay + (y - ay) * factor]);
  } else if (gesture.type === 'shape') {
    gesture.now = snapped(world);
  } else {
    gesture.now = world;
  }
  draw();
}

async function onPointerUp() {
  const gesture = state.gesture;
  state.gesture = null;
  state.snapMark = null;
  if (!gesture) return;
  if (gesture.type === 'guide') {
    guideUp(gesture);
    return;
  }
  if (gesture.type === 'node') {
    if (gesture.moved) refreshCurve(nodeVector());
    return;
  }
  if (gesture.type === 'move' || gesture.type === 'scale') {
    if (gesture.moved) {
      const finished = snapshot();
      const base = gesture.base || gesture.start;
      const current = JSON.parse(finished);
      for (const vector of current.vectors) {
        if (base.has(vector.id)) {
          vector.points = base.get(vector.id).points;
          vector.anchors = base.get(vector.id).anchors;
        }
      }
      state.history.push(JSON.stringify(current));
      state.future = [];
      if (gesture.type === 'scale') dropTextRecords();
      changed();
    }
    return;
  }
  if (gesture.type === 'marquee') {
    const x0 = Math.min(gesture.start[0], gesture.now[0]), x1 = Math.max(gesture.start[0], gesture.now[0]);
    const y0 = Math.min(gesture.start[1], gesture.now[1]), y1 = Math.max(gesture.start[1], gesture.now[1]);
    if (x1 - x0 > 0 || y1 - y0 > 0) {
      for (const vector of state.job.vectors) {
        const b = state.bounds.get(vector.id);
        if (b[0] >= x0 && b[2] <= x1 && b[1] >= y0 && b[3] <= y1) state.selection.add(vector.id);
      }
    }
    renderDesign();
    renderForm();
    draw();
    return;
  }
  if (gesture.type === 'shape') await finishShape(gesture);
}

async function finishShape(gesture) {
  const [x0, y0] = gesture.start;
  const [x1, y1] = gesture.now;
  const span = Math.hypot(x1 - x0, y1 - y0);
  draw();
  if (span * state.view.scale < 4) return;
  const tool = state.tool;
  let params;
  if (tool === 'rectangle') {
    params = { x: Math.min(x0, x1), y: Math.min(y0, y1), width: Math.abs(x1 - x0), height: Math.abs(y1 - y0) };
  } else if (tool === 'ellipse') {
    params = { cx: x0, cy: y0, rx: Math.max(Math.abs(x1 - x0), 0.1), ry: Math.max(Math.abs(y1 - y0), 0.1) };
  } else {
    params = { cx: x0, cy: y0, radius: span };
  }
  try {
    const { vectors } = await call('/api/shape', { kind: tool, params });
    addVectors(vectors);
    setTool('select');
  } catch (error) {
    say(error.message, true);
  }
}

function addVectors(vectors, group = '', keepLayer = false) {
  remember();
  for (const vector of vectors) {
    vector.group = group || vector.group || '';
    if (!keepLayer) vector.layer = state.activeLayer;
  }
  state.job.vectors.push(...vectors);
  state.selection = new Set(vectors.map((v) => v.id));
  changed();
}

function transformSelection(transform, keepText = false) {
  remember();
  if (!keepText) dropTextRecords();
  for (const vector of state.job.vectors) {
    if (state.selection.has(vector.id)) {
      vector.points = vector.points.map(transform);
      if (vector.anchors) vector.anchors = vector.anchors.map(transform);
    }
  }
  changed();
}

function removeSelection() {
  if (!state.selection.size) return;
  remember();
  const gone = state.selection;
  state.job.vectors = state.job.vectors.filter((v) => !gone.has(v.id));
  for (const op of state.job.operations) op.vector_ids = op.vector_ids.filter((id) => !gone.has(id));
  state.selection = new Set();
  changed();
}

function duplicateSelection() {
  if (!state.selection.size) return;
  const shift = inches() ? MM / 4 : 5;
  const group = newId();
  const copies = state.job.vectors
    .filter((v) => state.selection.has(v.id))
    .map((v) => ({
      ...v,
      id: newId(),
      group,
      points: v.points.map(([x, y]) => [x + shift, y - shift]),
      anchors: v.anchors ? v.anchors.map(([x, y]) => [x + shift, y - shift]) : null,
    }));
  addVectors(copies, group, true);
}

async function runEdit(operation, extra = {}) {
  const chosen = chosenVectors();
  if (!chosen.length) {
    say('Select vectors first.', true);
    return;
  }
  try {
    const { vectors, removed } = await call('/api/edit', { operation, vectors: chosen, ...extra });
    const fresh = vectors.filter((vector) => !state.selection.has(vector.id));
    const group = fresh.length > 1 ? newId() : '';
    for (const vector of fresh) vector.group = group;
    if (operation === 'offset') {
      addVectors(vectors, group, true);
      return;
    }
    dropTextRecords();
    replaceVectors(chosen.map((vector) => vector.id), vectors);
    if (removed && removed.length) say(`Removed ${removed.length} duplicate vector${removed.length === 1 ? '' : 's'}.`);
  } catch (error) {
    say(error.message, true);
  }
}

const TOOL_HINTS = {
  line: 'Click two points.',
  polyline: 'Click points. Click the first point to close. Double-click or press Enter to finish.',
  curve: 'Click points. Click the first point to close. Double-click or press Enter to finish.',
  arc: 'Click the start, the end, then a point on the arc.',
  node: 'Click a vector, then drag its nodes. Double-click a span to add a node. Delete removes the picked node.',
  trim: 'Click the span to remove. It ends at the nearest crossings.',
  extend: 'Click near the open end to extend it to the next vector.',
  measure: 'Click two points to add a dimension.',
};

function setTool(tool) {
  state.tool = tool;
  state.pen = null;
  state.measure = null;
  state.snapMark = null;
  if (tool !== 'node') state.nodeId = null;
  say(TOOL_HINTS[tool] || '');
  $$('[data-tool]').forEach((button) => button.classList.toggle('active', button.dataset.tool === tool));
  canvas.style.cursor = tool === 'select' ? 'default' : 'crosshair';
  if (tool === 'text') openForm('text');
  draw();
}

function setTab(name) {
  if (name === 'job') openForm('job');
  else if (name === 'paths') setDock('paths');
  else closeForm();
}

function renderJob() {
  const { material, safe_z, spoilboard_allowance, units, z_zero, xy_origin } = state.job;
  $('#job-width').value = trim(toUnits(material.width));
  $('#job-height').value = trim(toUnits(material.height));
  $('#job-thickness').value = trim(toUnits(material.thickness));
  $('#job-safe').value = trim(toUnits(safe_z));
  $('#job-allow').value = trim(toUnits(spoilboard_allowance));
  $('#job-units').value = units;
  $('#job-z').value = z_zero;
  $('#job-xy').value = xy_origin;
  $('#job-arcs').checked = state.job.arc_output;
  const tile = state.job.tiling;
  $('#tile-on').checked = Boolean(tile.enabled);
  $('#tile-w').value = tile.width ? trim(toUnits(tile.width)) : '';
  $('#tile-h').value = tile.height ? trim(toUnits(tile.height)) : '';
  $('#tile-overlap').value = trim(toUnits(tile.overlap || 0));
  renderMachines();
  if (!$('#text-height').value) $('#text-height').value = trim(toUnits(MM));
}

function renderDesign() {
  renderKerning();
  renderReference();
  const box = selectionBounds();
  $('#selection-empty').hidden = Boolean(box);
  $('#selection-tools').hidden = !box;
  if (!box) return;
  const count = state.selection.size;
  $('#selection-title').textContent = `${count} vector${count === 1 ? '' : 's'} selected`;
  $('#sel-x').value = trim(toUnits(box[0]));
  $('#sel-y').value = trim(toUnits(box[1]));
  $('#sel-w').value = trim(toUnits(box[2] - box[0]));
  $('#sel-h').value = trim(toUnits(box[3] - box[1]));
}

const CLIMB = { key: 'climb', label: 'Climb cut', type: 'check', initial: true };

const REPEAT_FIELDS = [
  { key: 'array_x', label: 'Copies across', type: 'count', initial: 1 },
  { key: 'array_y', label: 'Copies up', type: 'count', initial: 1 },
  { key: 'array_step_x', label: 'Step across', type: 'length', initial: 0 },
  { key: 'array_step_y', label: 'Step up', type: 'length', initial: 0 },
];

const OPERATIONS = {
  profile: {
    label: 'Profile',
    kinds: ['endmill'],
    fields: [
      { key: 'depth', label: 'Cut depth', type: 'length', initial: () => state.job.material.thickness, section: 'Cutting depth' },
      { key: 'side', label: 'Side', type: 'select', options: [['outside', 'Outside'], ['inside', 'Inside'], ['on', 'On the line']], initial: 'outside', section: 'Machine vectors' },
      { key: 'allowance', label: 'Allowance', type: 'length', initial: 0, section: 'Machine vectors' },
      { ...CLIMB, section: 'Machine vectors' },
      { key: 'ramp_length', label: 'Ramp length', type: 'length', initial: 0, section: 'Ramps and leads' },
      { key: 'lead', label: 'Lead in and out', type: 'select', options: [['none', 'None'], ['arc', 'Arc'], ['line', 'Line']], initial: 'none', section: 'Ramps and leads' },
      { key: 'lead_length', label: 'Lead length', type: 'length', initial: () => (inches() ? 0.25 * MM : 6), when: (p) => p.lead !== 'none', section: 'Ramps and leads' },
      { key: 'tab_count', label: 'Tabs', type: 'count', initial: 0, section: 'Tabs' },
      { key: 'tab_length', label: 'Tab length', type: 'length', initial: () => (inches() ? 0.5 * MM : 12), section: 'Tabs' },
      { key: 'tab_thickness', label: 'Tab thickness', type: 'length', initial: () => (inches() ? 0.125 * MM : 3), section: 'Tabs' },
    ],
  },
  pocket: {
    label: 'Pocket',
    kinds: ['endmill'],
    fields: [
      { key: 'depth', label: 'Cut depth', type: 'length', initial: () => (inches() ? 0.25 * MM : 6), section: 'Cutting depth' },
      { key: 'allowance', label: 'Allowance', type: 'length', initial: 0, section: 'Pocket options' },
      { key: 'finish_allowance', label: 'Finish pass stock', type: 'length', initial: 0, section: 'Pocket options' },
      { ...CLIMB, section: 'Pocket options' },
      { key: 'entry', label: 'Entry', type: 'select', options: [['plunge', 'Plunge'], ['ramp', 'Ramp'], ['helix', 'Helix']], initial: 'plunge', section: 'Entry' },
      { key: 'entry_angle', label: 'Entry angle (deg)', type: 'plain', initial: 3, when: (p) => p.entry !== 'plunge', section: 'Entry' },
    ],
  },
  vcarve: {
    label: 'V-carve',
    kinds: ['vbit'],
    fields: [
      { key: 'start_depth', label: 'Start depth', type: 'length', initial: '', section: 'Cutting depth' },
      { key: 'flat_depth', label: 'Flat depth (blank for none)', type: 'length', initial: '', section: 'Cutting depth' },
      { key: 'clear_tool_id', label: 'Clearing tool', type: 'clearing', initial: '', section: 'Flat area clearance' },
    ],
  },
  drill: {
    label: 'Drill',
    kinds: ['drill', 'endmill'],
    fields: [
      { key: 'depth', label: 'Depth', type: 'length', initial: () => state.job.material.thickness },
      { key: 'peck', label: 'Peck depth (0 for none)', type: 'length', initial: 0 },
    ],
  },
  chamfer: {
    label: 'Chamfer',
    kinds: ['vbit'],
    fields: [
      { key: 'side', label: 'Side', type: 'select', options: [['outside', 'Outside edge'], ['inside', 'Inside edge'], ['on', 'On the line']], initial: 'outside' },
      { key: 'width', label: 'Chamfer width', type: 'length', initial: () => (inches() ? 0.125 * MM : 3) },
      { key: 'clearance', label: 'Tip clearance', type: 'length', initial: 0 },
      CLIMB,
    ],
  },
  fluting: {
    label: 'Fluting',
    kinds: ['ballnose', 'vbit', 'endmill'],
    fields: [
      { key: 'depth', label: 'Flute depth', type: 'length', initial: () => (inches() ? 0.125 * MM : 3) },
      { key: 'ramp', label: 'Ramp', type: 'select', options: [['both', 'Both ends'], ['start', 'Start'], ['end', 'End'], ['none', 'None']], initial: 'both' },
      { key: 'ramp_length', label: 'Ramp length', type: 'length', initial: () => (inches() ? MM : 25) },
      { key: 'smooth', label: 'Smooth ramp', type: 'check', initial: false },
    ],
  },
  prism: {
    label: 'Raised prism',
    kinds: ['vbit'],
    fields: [
      { key: 'max_depth', label: 'Depth limit (blank for none)', type: 'length', initial: '' },
    ],
  },
  auto_inlay: {
    label: 'Auto-inlay',
    kinds: ['endmill'],
    generator: 'auto',
    fields: [
      { key: 'depth', label: 'Pocket depth', type: 'length', initial: () => (inches() ? 0.125 * MM : 3) },
      { key: 'clearance', label: 'Fit clearance', type: 'length', initial: () => (inches() ? 0.004 * MM : 0.1) },
      { key: 'plug_extra', label: 'Plug extra depth', type: 'length', initial: () => (inches() ? 0.02 * MM : 0.5) },
    ],
  },
  vcarve_inlay: {
    label: 'V-carve inlay',
    kinds: ['vbit'],
    generator: 'vcarve',
    fields: [
      { key: 'depth', label: 'Pocket depth', type: 'length', initial: () => (inches() ? 0.125 * MM : 3) },
      { key: 'gap', label: 'Glue gap', type: 'length', initial: () => (inches() ? 0.03 * MM : 0.75) },
      { key: 'margin', label: 'Plug border', type: 'length', initial: () => (inches() ? 0.5 * MM : 12) },
      { key: 'clear_tool_id', label: 'Clearing tool', type: 'clearing', initial: '' },
    ],
  },
  merge: {
    label: 'Merged toolpath',
    kinds: ['endmill', 'ballnose', 'vbit', 'drill'],
    hidden: true,
    fields: [],
  },
};

function startOperation(kind) {
  const spec = OPERATIONS[kind];
  const params = {};
  const fields = spec.generator ? spec.fields : [...spec.fields, ...REPEAT_FIELDS];
  for (const field of fields) {
    params[field.key] = typeof field.initial === 'function' ? field.initial() : field.initial;
  }
  const tool = filteredTools().find((t) => spec.kinds.includes(t.kind)) || state.job.tools.find((t) => spec.kinds.includes(t.kind));
  state.draft = { kind, name: spec.label, tool_id: tool.id, params, vector_ids: [], id: null, enabled: true };
  state.activeOp = null;
  renderForm();
  renderOperations();
}

function editOperation(op) {
  const params = { ...op.params };
  state.draft = { ...op, params };
  state.activeOp = op.id;
  renderForm();
  renderOperations();
}

function labelled(text, control) {
  const label = document.createElement('label');
  label.append(document.createTextNode(text), control);
  return label;
}

function fieldControl(field, params) {
  if (field.type === 'select' || field.type === 'clearing') {
    const select = document.createElement('select');
    const options = field.type === 'select'
      ? field.options
      : [['', 'None'], ...state.job.tools.filter((t) => t.kind === 'endmill').map((t) => [t.id, t.name])];
    for (const [value, text] of options) select.add(new Option(text, value));
    select.value = params[field.key] ?? '';
    select.addEventListener('change', () => {
      params[field.key] = select.value;
      if (field.type === 'select') renderForm();
    });
    return labelled(field.label, select);
  }
  const input = document.createElement('input');
  if (field.type === 'check') {
    input.type = 'checkbox';
    input.checked = Boolean(params[field.key]);
    input.addEventListener('change', () => { params[field.key] = input.checked; });
    const label = labelled(` ${field.label}`, input);
    label.prepend(input);
    label.className = 'check';
    return label;
  }
  input.type = 'number';
  input.step = 'any';
  input.min = '0';
  const value = params[field.key];
  if (field.type === 'count') {
    input.step = '1';
    input.value = value ?? field.initial;
    input.addEventListener('change', () => { params[field.key] = Math.max(0, Math.round(Number(input.value) || 0)); });
    return labelled(field.label, input);
  }
  if (field.type === 'plain') {
    input.value = value ?? '';
    input.addEventListener('change', () => { params[field.key] = input.value === '' ? '' : Number(input.value); });
    return labelled(field.label, input);
  }
  input.value = value === '' || value === undefined || value === null ? '' : trim(toUnits(value));
  input.addEventListener('change', () => {
    params[field.key] = input.value === '' ? '' : toMm(Number(input.value));
  });
  return labelled(`${field.label} (${unitLabel()})`, input);
}

function renderForm() {
  const host = $('#op-form');
  host.replaceChildren();
  const draft = state.draft;
  $('#paths-home').hidden = Boolean(draft);
  if (!draft) return;
  setDock('paths');
  const spec = OPERATIONS[draft.kind];
  const form = document.createElement('div');
  form.className = 'form';
  const head = document.createElement('div');
  head.className = 'form-head';
  const title = document.createElement('h3');
  title.textContent = draft.id ? `Edit ${draft.name}` : spec.label;
  const dismiss = document.createElement('button');
  dismiss.textContent = 'Close';
  dismiss.addEventListener('click', closeOperation);
  head.append(title, dismiss);
  form.append(head);

  const nameInput = document.createElement('input');
  nameInput.value = draft.name;
  nameInput.addEventListener('input', () => { draft.name = nameInput.value; });
  const nameLabel = labelled('Toolpath name', nameInput);
  nameLabel.className = 'stack';

  const toolSelect = document.createElement('select');
  const choices = filteredTools().filter((t) => spec.kinds.includes(t.kind));
  const current = state.job.tools.find((t) => t.id === draft.tool_id);
  if (current && !choices.includes(current)) choices.unshift(current);
  for (const tool of choices) toolSelect.add(new Option(tool.name, tool.id));
  toolSelect.value = draft.tool_id;
  toolSelect.disabled = draft.kind === 'merge';
  toolSelect.addEventListener('change', () => { draft.tool_id = toolSelect.value; });
  const toolLabel = labelled('Tool', toolSelect);
  toolLabel.className = 'stack';
  form.append(toolLabel);
  if (draft.kind !== 'merge') {
    const toolRow = document.createElement('div');
    toolRow.className = 'row';
    const editTools = document.createElement('button');
    editTools.textContent = 'Edit tools';
    editTools.addEventListener('click', openTools);
    toolRow.append(editTools);
    form.append(toolRow);
  }

  let grid = null;
  let section = null;
  for (const field of spec.fields) {
    if (field.when && !field.when(draft.params)) continue;
    if (!grid || (field.section && field.section !== section)) {
      if (field.section) {
        const heading = document.createElement('h4');
        heading.textContent = field.section;
        form.append(heading);
      }
      section = field.section || section;
      grid = document.createElement('div');
      grid.className = 'grid';
      form.append(grid);
    }
    grid.append(fieldControl(field, draft.params));
  }
  if (!spec.generator && draft.kind !== 'merge') {
    const repeat = document.createElement('details');
    const heading = document.createElement('summary');
    heading.textContent = 'Repeat this toolpath';
    const repeatGrid = document.createElement('div');
    repeatGrid.className = 'grid';
    for (const field of REPEAT_FIELDS) repeatGrid.append(fieldControl(field, draft.params));
    repeat.append(heading, repeatGrid);
    repeat.open = Number(draft.params.array_x || 1) > 1 || Number(draft.params.array_y || 1) > 1;
    form.append(repeat);
  }

  form.append(nameLabel);

  const note = document.createElement('p');
  note.className = 'hint';
  const count = state.selection.size;
  note.textContent = draft.id
    ? `Uses ${draft.vector_ids.length} vectors. ${count ? `${count} are selected now.` : ''}`
    : `${count} vector${count === 1 ? '' : 's'} selected on the sheet.`;
  form.append(note);

  const row = document.createElement('div');
  row.className = 'row';
  const apply = document.createElement('button');
  apply.className = 'primary';
  apply.id = 'op-apply';
  apply.textContent = spec.generator ? 'Create pocket and plug' : 'Calculate';
  apply.addEventListener('click', () => (spec.generator ? runInlay(spec.generator, draft) : applyOperation()));
  row.append(apply);
  if (draft.kind === 'merge') {
    const split = document.createElement('button');
    split.textContent = 'Split';
    split.addEventListener('click', () => splitMerged(state.job.operations.find((op) => op.id === draft.id)));
    row.append(split);
  }
  if (draft.id && draft.kind !== 'merge') {
    const swap = document.createElement('button');
    swap.textContent = 'Use selection';
    swap.addEventListener('click', () => {
      if (state.selection.size) {
        draft.vector_ids = [...state.selection];
        renderForm();
      }
    });
    row.append(swap);
  }
  form.append(row);
  host.append(form);
}

function closeOperation() {
  state.draft = null;
  state.activeOp = null;
  renderForm();
  renderOperations();
}

function cleanParams(params) {
  const cleaned = {};
  for (const [key, value] of Object.entries(params)) {
    if (value !== '' && value !== null && value !== undefined) cleaned[key] = value;
  }
  return cleaned;
}

function applyOperation() {
  const draft = state.draft;
  const ids = draft.id ? draft.vector_ids : [...state.selection];
  if (!ids.length) {
    say('Select one or more vectors first.', true);
    return;
  }
  remember();
  const record = {
    kind: draft.kind,
    tool_id: draft.tool_id,
    vector_ids: ids,
    params: cleanParams(draft.params),
    name: draft.name,
    id: draft.id || newId(),
    enabled: draft.enabled !== false,
  };
  const index = state.job.operations.findIndex((op) => op.id === record.id);
  if (index >= 0) state.job.operations[index] = record;
  else state.job.operations.push(record);
  state.draft = null;
  state.activeOp = null;
  renderForm();
  changed();
}

function moveOperation(op, step) {
  const list = state.job.operations;
  const from = list.indexOf(op), to = from + step;
  if (to < 0 || to >= list.length) return;
  remember();
  [list[from], list[to]] = [list[to], list[from]];
  changed();
}

function renderOperations() {
  const list = $('#op-list');
  list.replaceChildren();
  const computed = state.computed ? state.computed.paths : [];
  for (const op of state.job.operations) {
    const item = document.createElement('li');
    item.classList.toggle('active', state.activeOp === op.id);
    const head = document.createElement('div');
    head.className = 'op-head';
    const name = document.createElement('span');
    name.className = 'op-name';
    name.textContent = op.name || OPERATIONS[op.kind].label;
    const enabled = document.createElement('input');
    enabled.type = 'checkbox';
    enabled.checked = op.enabled !== false;
    enabled.title = 'Include in the output';
    enabled.addEventListener('click', (event) => event.stopPropagation());
    enabled.addEventListener('change', () => {
      remember();
      op.enabled = enabled.checked;
      changed();
    });
    head.append(enabled, name);
    for (const [text, tip, handler] of [
      ['\u2191', 'Move up', () => moveOperation(op, -1)],
      ['\u2193', 'Move down', () => moveOperation(op, 1)],
      ['\u00d7', 'Delete this toolpath', () => {
        remember();
        state.job.operations = state.job.operations.filter((other) => other !== op);
        changed();
      }],
    ]) {
      const button = document.createElement('button');
      button.textContent = text;
      button.title = tip;
      button.setAttribute('aria-label', tip);
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        handler();
      });
      head.append(button);
    }
    item.append(head);
    const sub = document.createElement('div');
    sub.className = 'op-sub';
    const tool = state.job.tools.find((t) => t.id === op.tool_id);
    sub.textContent = `${OPERATIONS[op.kind].label}, ${tool ? tool.name : 'no tool'}, ${op.vector_ids.length} vectors`;
    const pick = document.createElement('input');
    pick.type = 'checkbox';
    pick.className = 'op-pick';
    pick.title = 'Pick for merge or template';
    pick.checked = state.checked.has(op.id);
    pick.addEventListener('click', (event) => event.stopPropagation());
    pick.addEventListener('change', () => {
      if (pick.checked) state.checked.add(op.id);
      else state.checked.delete(op.id);
    });
    sub.prepend(pick);
    item.append(sub);
    for (const path of computed.filter((p) => p.operation_id === op.id)) {
      for (const warning of path.warnings) {
        const line = document.createElement('div');
        line.className = 'op-warn';
        line.textContent = warning;
        item.append(line);
      }
    }
    item.addEventListener('click', () => editOperation(op));
    list.append(item);
  }
  if (!state.job.operations.length) {
    const empty = document.createElement('li');
    empty.className = 'hint';
    empty.textContent = 'Select vectors, then pick a toolpath type above.';
    list.append(empty);
  }
  const programs = state.computed ? state.computed.programs : [];
  const seconds = programs.reduce((sum, row) => sum + row.seconds, 0);
  const failed = programs.filter((row) => !row.passed).length;
  $('#status-time').textContent = programs.length ? `Estimated run time ${formatTime(seconds)}` : '';
  $('#op-summary').textContent = programs.length
    ? `${programs.length} tool${programs.length === 1 ? '' : 's'}. ${failed ? `${failed} failed the checker.` : 'All passed the checker.'}`
    : '';
}

function formatTime(seconds) {
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${Math.max(minutes, 1)} min`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function reportNode(title, report, action) {
  const node = document.createElement('div');
  node.className = 'report';
  const head = document.createElement('div');
  head.className = 'report-head';
  const name = document.createElement('span');
  name.className = 'report-name';
  name.textContent = title;
  const badge = document.createElement('span');
  badge.className = `badge ${report.passed ? 'pass' : 'fail'}`;
  badge.textContent = report.passed ? 'Passed checker' : 'Failed checker';
  head.append(name, badge);
  if (action) head.append(action);
  node.append(head);
  const meta = document.createElement('div');
  meta.className = 'report-meta';
  meta.textContent = `${formatTime(report.seconds)}, deepest ${trim(toUnits(report.z_min))} ${unitLabel()}, ${report.line_count} lines`;
  node.append(meta);
  if (report.errors.length) {
    const list = document.createElement('ul');
    for (const error of report.errors) {
      const item = document.createElement('li');
      item.textContent = error;
      list.append(item);
    }
    node.append(list);
  }
  for (const warning of report.warnings || []) {
    const line = document.createElement('div');
    line.className = 'op-warn';
    line.textContent = warning;
    node.append(line);
  }
  return node;
}

function showPreview() {
  if (state.previewMode === 'shaded') showShaded();
  else showOrbit();
}

async function runPreview() {
  const line = $('#preview-state');
  if (!state.job.operations.some((op) => op.enabled !== false)) {
    state.surface = null;
    $('#preview-report').replaceChildren();
    line.textContent = 'Create a toolpath, then preview it here.';
    return;
  }
  const token = ++state.previewToken;
  line.textContent = 'Simulating the cut from the G-code';
  try {
    const result = await call('/api/preview', { job: state.job, post: $('#post-select').value });
    if (token !== state.previewToken) return;
    $('#preview-image').src = result.image;
    state.surface = result.surface;
    state.surfaceStale = false;
    line.textContent = 'Read from the G-code. Drag to orbit, scroll to zoom.';
    $('#preview-report').replaceChildren(...result.programs.map((row) => reportNode(row.tile ? `${row.tool}, ${row.tile}` : row.tool, row)));
    if (state.viewMode === '3d') showPreview();
  } catch (error) {
    line.textContent = error.message;
  }
}

function openPreview() {
  if (!state.job.operations.length) {
    say('Create a toolpath first.', true);
    return;
  }
  state.surfaceStale = true;
  setView('3d');
}

function showShaded() {
  state.previewMode = 'shaded';
  $('#preview-image').hidden = false;
  $('#orbit-canvas').hidden = true;
  $('#view-shaded').classList.add('active');
  $('#view-orbit').classList.remove('active');
}

function showOrbit() {
  state.previewMode = 'orbit';
  $('#preview-image').hidden = true;
  $('#orbit-canvas').hidden = false;
  $('#view-shaded').classList.remove('active');
  $('#view-orbit').classList.add('active');
  if (state.surface && !orbit.load($('#orbit-canvas'), state.surface, state.job.material.thickness)) {
    showShaded();
    $('#preview-state').textContent = 'This view cannot start 3D graphics, so the shaded view stays on.';
  }
}

async function openExport() {
  if (!state.job.operations.length) {
    say('Create a toolpath first.', true);
    return;
  }
  const dialog = $('#export-dialog');
  const list = $('#export-list');
  list.replaceChildren();
  if (!dialog.open) dialog.showModal();
  try {
    const { files } = await call('/api/gcode', {
      job: state.job,
      post: $('#post-select').value,
      title: $('#job-title').value || 'job',
    });
    list.replaceChildren(...files.map((file) => {
      const save = document.createElement('button');
      save.className = 'primary';
      save.textContent = 'Save';
      save.disabled = !file.report.passed;
      save.addEventListener('click', () => platform.saveTextFile(file.name, file.text));
      return reportNode(file.name, file.report, save);
    }));
    if (!files.length) list.textContent = 'No toolpath produced any moves.';
  } catch (error) {
    list.textContent = error.message;
  }
}

async function saveProject() {
  const text = JSON.stringify({ format: 'downcut', version: FILE_VERSION, job: state.job });
  platform.saveTextFile(`${$('#job-title').value || 'job'}.downcut`, text);
}

function loadJob(job) {
  state.job = normalizeJob(job);
  state.activeLayer = state.job.layers[0].name;
  state.nodeId = null;
  state.refId = null;
  state.issueMark = null;
  state.checked = new Set();
  $('#issue-list').replaceChildren();
  syncPosts();
  renderDimensions();
  state.selection = new Set();
  state.history = [];
  state.future = [];
  state.draft = null;
  state.activeOp = null;
  state.computed = null;
  state.cutPath = state.rapidPath = null;
  renderJob();
  renderForm();
  changed();
  fit();
}

function mayReplaceJob() {
  if (!state.job.vectors.length) return Promise.resolve(true);
  return platform.confirm('Replace the current job? Save first if you want to keep it.');
}

async function openProject() {
  if (!(await mayReplaceJob())) return;
  const file = await platform.openTextFile('.downcut,.kerfline,.json');
  if (!file) return;
  try {
    const data = JSON.parse(file.text);
    if (!['downcut', 'kerfline'].includes(data.format)) throw new Error('That is not a Downcut project.');
    loadJob(data.job);
    say(`Opened ${file.name}`);
  } catch (error) {
    say(error.message, true);
  }
}

async function importFile() {
  const file = await platform.openBinaryFile('.svg,.dxf,.pdf,.ai,.eps');
  if (!file) return;
  try {
    const { vectors } = await call('/api/import', { name: file.name, data: file.base64, units: state.job.units });
    const { width, height } = state.job.material;
    const xs = vectors.flatMap((v) => v.points.map((p) => p[0]));
    const ys = vectors.flatMap((v) => v.points.map((p) => p[1]));
    const dx = (width - Math.max(...xs)) / 2, dy = (height - Math.max(...ys)) / 2;
    for (const vector of vectors) vector.points = vector.points.map(([x, y]) => [x + dx, y + dy]);
    addVectors(vectors, newId());
    setTab('design');
    say(`Imported ${vectors.length} vectors from ${file.name}`);
  } catch (error) {
    say(error.message, true);
  }
}

function resizeSelection() {
  const box = selectionBounds();
  if (!box) return;
  const width = box[2] - box[0], height = box[3] - box[1];
  let newW = toMm(Number($('#sel-w').value)), newH = toMm(Number($('#sel-h').value));
  if (!(newW > 0) || !(newH > 0)) return;
  if ($('#sel-lock').checked) {
    const factor = Math.abs(newW - width) > 1e-6 ? newW / width : newH / height;
    newW = width * factor;
    newH = height * factor;
  }
  const fx = width ? newW / width : 1, fy = height ? newH / height : 1;
  transformSelection(([x, y]) => [box[0] + (x - box[0]) * fx, box[1] + (y - box[1]) * fy]);
}

function positionSelection() {
  const box = selectionBounds();
  if (!box) return;
  const dx = toMm(Number($('#sel-x').value)) - box[0];
  const dy = toMm(Number($('#sel-y').value)) - box[1];
  transformSelection(([x, y]) => [x + dx, y + dy], true);
}

function rotateSelection() {
  const box = selectionBounds();
  if (!box) return;
  const angle = (Number($('#sel-rot').value) * Math.PI) / 180;
  const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
  const c = Math.cos(angle), s = Math.sin(angle);
  transformSelection(([x, y]) => [cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c]);
}

function mirrorSelection(horizontal) {
  const box = selectionBounds();
  if (!box) return;
  const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
  transformSelection(([x, y]) => (horizontal ? [2 * cx - x, y] : [x, 2 * cy - y]));
}

function centerSelection() {
  const box = selectionBounds();
  if (!box) return;
  const dx = state.job.material.width / 2 - (box[0] + box[2]) / 2;
  const dy = state.job.material.height / 2 - (box[1] + box[3]) / 2;
  transformSelection(([x, y]) => [x + dx, y + dy], true);
}

function bindJobField(selector, apply) {
  $(selector).addEventListener('change', (event) => {
    apply(event.target.value);
    renderJob();
    changed();
    fit();
  });
}

function bindUi() {
  decorateButtons();
  bindShell();
  $$('[data-tool]').forEach((button) => button.addEventListener('click', () => setTool(button.dataset.tool)));
  $$('dialog [data-close]').forEach((button) => button.addEventListener('click', () => button.closest('dialog').close()));

  const newOp = $('#new-op');
  for (const [kind, spec] of Object.entries(OPERATIONS)) {
    if (spec.hidden) continue;
    const button = document.createElement('button');
    button.textContent = spec.label;
    newOp.append(button);
    addIcon(button, kind);
    button.addEventListener('click', () => startOperation(kind));
  }

  $('#btn-new').addEventListener('click', async () => {
    if (!(await mayReplaceJob())) return;
    loadJob(newJob());
    setTab('job');
  });
  $('#btn-sample').addEventListener('click', async () => {
    if (!(await mayReplaceJob())) return;
    say('Loading the sample sign');
    loadJob(await call('/api/sample'));
    setTab('paths');
  });
  $('#btn-open').addEventListener('click', openProject);
  $('#btn-save').addEventListener('click', saveProject);
  $('#btn-import').addEventListener('click', importFile);
  $('#btn-undo').addEventListener('click', undo);
  $('#btn-redo').addEventListener('click', redo);
  $('#btn-preview').addEventListener('click', openPreview);
  $('#btn-export').addEventListener('click', openExport);
  $('#btn-fit').addEventListener('click', fit);
  $('#show-paths').addEventListener('change', draw);
  $('#show-rapids').addEventListener('change', draw);
  $('#show-rulers').addEventListener('change', (event) => { state.rulers = event.target.checked; draw(); });
  $('#snap-smart').addEventListener('change', (event) => { state.snap.smart = event.target.checked; });
  $('#snap-grid').addEventListener('change', (event) => { state.snap.grid = event.target.checked; });
  $('#snap-step').addEventListener('change', (event) => { state.snap.step = toMm(Number(event.target.value)) || state.snap.step; });
  $('#post-select').addEventListener('change', () => {
    scheduleCompute();
    if ($('#export-dialog').open) openExport();
  });
  $('#job-title').addEventListener('change', () => { if ($('#export-dialog').open) openExport(); });
  $('#btn-sheet').addEventListener('click', openSheet);
  $('#view-shaded').addEventListener('click', showShaded);
  $('#view-orbit').addEventListener('click', showOrbit);
  $('#sheet-save').addEventListener('click', () => { if (state.sheet) platform.saveTextFile(state.sheet.name, state.sheet.html); });
  $('#sheet-print').addEventListener('click', () => platform.printFrame($('#sheet-frame')));
  $('#btn-machine').addEventListener('click', importMachine);
  $('#btn-tools').addEventListener('click', openTools);
  $('#btn-posts').addEventListener('click', () => openPosts());
  $('#job-machine').addEventListener('change', (event) => {
    const rows = [...state.library.machines, state.job.machine];
    setMachine(rows.find((row) => row.name === event.target.value) || {});
    renderJob();
    changed();
  });
  $('#job-arcs').addEventListener('change', (event) => { state.job.arc_output = event.target.checked; changed(); });
  $('#tile-on').addEventListener('change', (event) => {
    const tile = state.job.tiling;
    tile.enabled = event.target.checked;
    if (tile.enabled && !tile.width) {
      tile.width = state.job.machine.width || state.job.material.width;
      tile.height = state.job.machine.height || state.job.material.height;
    }
    renderJob();
    changed();
  });
  for (const [selector, key] of [['#tile-w', 'width'], ['#tile-h', 'height'], ['#tile-overlap', 'overlap']]) {
    $(selector).addEventListener('change', (event) => {
      state.job.tiling[key] = toMm(Number(event.target.value));
      changed();
    });
  }
  $('#filter-material').addEventListener('change', (event) => {
    state.filter.material = event.target.value;
    renderToolList();
    renderForm();
  });
  $('#filter-machine').addEventListener('change', (event) => {
    state.filter.machine = event.target.value;
    renderToolList();
    renderForm();
  });
  $('#tool-add').addEventListener('click', addTool);
  $('#tool-remove').addEventListener('click', removeTool);
  $('#post-new').addEventListener('click', newPost);
  $('#post-import').addEventListener('click', importMachine);
  $('#post-export').addEventListener('click', exportPost);
  $('#post-check').addEventListener('click', checkPost);
  $('#post-remove').addEventListener('click', removePost);
  $('#layer-add').addEventListener('click', addLayer);
  $('#layer-move').addEventListener('click', moveToLayer);
  $('#layer-select').addEventListener('click', selectLayer);
  $('#layer-remove').addEventListener('click', removeLayer);
  $('#op-join').addEventListener('click', () => runEdit('join', { tolerance: toMm(Number($('#edit-tolerance').value)) }));
  $('#op-close').addEventListener('click', () => runEdit('close'));
  $('#op-smooth').addEventListener('click', () => runEdit('smooth', { tolerance: toMm(Number($('#edit-tolerance').value)) }));
  $('#op-simplify').addEventListener('click', () => runEdit('simplify', { tolerance: toMm(Number($('#edit-tolerance').value)) }));
  $('#op-dedupe').addEventListener('click', () => runEdit('dedupe', { tolerance: toMm(Number($('#edit-tolerance').value)) }));
  $('#op-fillet').addEventListener('click', () => runEdit('fillet', { radius: toMm(Number($('#edit-radius').value)) }));
  $('#op-dogbone').addEventListener('click', () => runEdit('dogbone', { diameter: toMm(Number($('#edit-bone').value)), side: $('#edit-side').value }));
  $('#op-tbone').addEventListener('click', () => runEdit('tbone', { diameter: toMm(Number($('#edit-bone').value)), side: $('#edit-side').value }));
  $('#ref-set').addEventListener('click', setReference);
  $('#op-boundary').addEventListener('click', () => {
    const boundary = referenceVector();
    if (!boundary) {
      say('Set a closed path as the boundary first.', true);
      return;
    }
    state.selection.delete(boundary.id);
    runEdit('trim_boundary', { boundary, keep: $('#edit-keep').value });
  });
  $('#grid-add').addEventListener('click', gridArray);
  $('#circle-add').addEventListener('click', circularArray);
  $('#path-add').addEventListener('click', pathArray);
  $('#nest-run').addEventListener('click', nestSelection);
  $('#check-run').addEventListener('click', checkVectors);
  $('#export-svg').addEventListener('click', () => exportVectors('svg'));
  $('#export-dxf').addEventListener('click', () => exportVectors('dxf'));
  $('#text-path').addEventListener('click', addTextOnPath);
  $('#op-merge').addEventListener('click', mergeChecked);
  $('#template-save').addEventListener('click', saveTemplate);
  $('#template-apply').addEventListener('click', applyTemplate);

  bindJobField('#job-width', (v) => { state.job.material.width = toMm(Number(v)); });
  bindJobField('#job-height', (v) => { state.job.material.height = toMm(Number(v)); });
  bindJobField('#job-thickness', (v) => { state.job.material.thickness = toMm(Number(v)); });
  bindJobField('#job-units', (v) => { state.job.units = v; });
  bindJobField('#job-z', (v) => { state.job.z_zero = v; });
  bindJobField('#job-xy', (v) => { state.job.xy_origin = v; });
  bindJobField('#job-safe', (v) => { state.job.safe_z = toMm(Number(v)); });
  bindJobField('#job-allow', (v) => { state.job.spoilboard_allowance = toMm(Number(v)); });

  $('#sel-x').addEventListener('change', positionSelection);
  $('#sel-y').addEventListener('change', positionSelection);
  $('#sel-w').addEventListener('change', resizeSelection);
  $('#sel-h').addEventListener('change', resizeSelection);
  $('#op-rotate').addEventListener('click', rotateSelection);
  $('#op-offset').addEventListener('click', () => runEdit('offset', { distance: toMm(Number($('#sel-offset').value)) }));
  $('#op-mirror-h').addEventListener('click', () => mirrorSelection(true));
  $('#op-mirror-v').addEventListener('click', () => mirrorSelection(false));
  $('#op-weld').addEventListener('click', () => runEdit('weld'));
  $('#op-subtract').addEventListener('click', () => runEdit('subtract'));
  $('#op-intersect').addEventListener('click', () => runEdit('intersect'));
  $('#op-center').addEventListener('click', centerSelection);
  $('#op-duplicate').addEventListener('click', duplicateSelection);
  $('#op-delete').addEventListener('click', removeSelection);
  $('#text-add').addEventListener('click', addText);

  canvas.addEventListener('pointerdown', onPointerDown);
  canvas.addEventListener('pointermove', onPointerMove);
  canvas.addEventListener('pointerup', onPointerUp);
  canvas.addEventListener('wheel', (event) => {
    event.preventDefault();
    if (event.ctrlKey || event.metaKey) {
      const [sx, sy] = pointerPosition(event);
      const factor = Math.exp(-event.deltaY * 0.01);
      const x = worldX(sx), y = worldY(sy);
      state.view.scale *= factor;
      state.view.ox = sx - x * state.view.scale;
      state.view.oy = sy + y * state.view.scale;
    } else {
      state.view.ox -= event.deltaX;
      state.view.oy -= event.deltaY;
    }
    draw();
  }, { passive: false });

  window.addEventListener('resize', resize);
  window.addEventListener('keyup', (event) => {
    if (event.code === 'Space') state.spaceDown = false;
  });
  window.addEventListener('keydown', onKey);
}

function onKey(event) {
  if (event.target.matches('input, textarea, select')) return;
  const command = event.metaKey || event.ctrlKey;
  const key = event.key.toLowerCase();
  if (event.code === 'Space') {
    state.spaceDown = true;
    event.preventDefault();
  } else if (key === 'enter' && state.pen) {
    finishPen(false);
  } else if (['f2', 'f3', 'f11', 'f12'].includes(key)) {
    event.preventDefault();
    RUN_ACTIONS[{ f2: 'view-2d', f3: 'view-3d', f11: 'layout-design', f12: 'layout-machine' }[key]]();
  } else if (command && key === 'z') {
    event.preventDefault();
    if (event.shiftKey) redo();
    else undo();
  } else if (command && key === 'd') {
    event.preventDefault();
    duplicateSelection();
  } else if (command && key === 'a') {
    event.preventDefault();
    state.selection = new Set(state.job.vectors.map((v) => v.id));
    renderDesign();
    draw();
  } else if (key === 'delete' || key === 'backspace') {
    event.preventDefault();
    if (!(state.tool === 'node' && removeNode())) removeSelection();
  } else if (key === 'escape') {
    state.selection = new Set();
    state.issueMark = null;
    setTool('select');
    renderDesign();
  } else if (!command) {
    const tool = {
      v: 'select', r: 'rectangle', e: 'ellipse', p: 'polygon', s: 'star', t: 'text',
      l: 'line', o: 'polyline', c: 'curve', a: 'arc', n: 'node', x: 'trim', j: 'extend', m: 'measure',
    }[key];
    if (tool) setTool(tool);
  }
}

async function start() {
  bindUi();
  state.boot = await call('/api/bootstrap');
  try {
    state.library = { posts: [], tools: [], machines: [], ...JSON.parse(platform.readLibrary() || '{}') };
  } catch {
    state.library = { posts: [], tools: [], machines: [] };
  }
  const fonts = $('#text-font');
  const single = document.createElement('optgroup');
  single.label = 'Single line, for engraving';
  for (const name of state.boot.stroke_fonts) single.append(new Option(name, name));
  const outline = document.createElement('optgroup');
  outline.label = 'Outline fonts';
  for (const name of state.boot.fonts) outline.append(new Option(name, name));
  fonts.append(single, outline);
  fonts.value = state.boot.fonts.includes('Georgia Bold') ? 'Georgia Bold' : state.boot.fonts[0];

  let job = null;
  const draft = platform.readDraft();
  if (draft) {
    try {
      job = JSON.parse(draft).job;
    } catch {
      job = null;
    }
  }
  setTool('select');
  setDock('paths');
  setView('2d');
  resize();
  loadJob(job || newJob());
  if (!state.job.vectors.length) openForm('job');
  if (!state.library.safetyAccepted) $('#safety-dialog').showModal();
}

start().catch((error) => say(error.message, true));
