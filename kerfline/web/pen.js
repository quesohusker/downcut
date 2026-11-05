'use strict';

const PEN_TOOLS = new Set(['line', 'polyline', 'curve', 'arc']);
const RULER = 20;

function layerOf(name) {
  return state.job.layers.find((layer) => layer.name === name);
}

function shownVector(vector) {
  const layer = layerOf(vector.layer);
  return !layer || layer.visible;
}

function pickableVector(vector) {
  const layer = layerOf(vector.layer);
  return !layer || (layer.visible && !layer.locked);
}

function vectorNodes(vector) {
  return vector.anchors || vector.points;
}

function snapPoint(world, ignore = null) {
  const reach = 9 / state.view.scale;
  let best = null, bestDistance = reach;
  const consider = (x, y, kind) => {
    const distance = Math.hypot(x - world[0], y - world[1]);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = { point: [x, y], kind };
    }
  };
  if (state.snap.smart) {
    for (const vector of state.job.vectors) {
      if (!shownVector(vector) || (ignore && ignore.has(vector.id))) continue;
      const b = state.bounds.get(vector.id);
      if (world[0] < b[0] - reach || world[0] > b[2] + reach || world[1] < b[1] - reach || world[1] > b[3] + reach) {
        consider((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, 'center');
        continue;
      }
      const nodes = vectorNodes(vector);
      if (nodes.length <= 600) {
        for (const [x, y] of nodes) consider(x, y, 'node');
      } else {
        consider(nodes[0][0], nodes[0][1], 'node');
        consider(nodes[nodes.length - 1][0], nodes[nodes.length - 1][1], 'node');
      }
      if (nodes.length <= 64) {
        const spans = nodes.length - 1 + (vector.closed ? 1 : 0);
        for (let i = 0; i < spans; i++) {
          const a = nodes[i], c = nodes[(i + 1) % nodes.length];
          consider((a[0] + c[0]) / 2, (a[1] + c[1]) / 2, 'middle');
        }
      }
      consider((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, 'center');
    }
    const { width, height } = state.job.material;
    for (const [x, y] of [[0, 0], [width, 0], [width, height], [0, height], [width / 2, height / 2]]) consider(x, y, 'sheet');
  }
  if (best) return best;
  let [x, y] = world;
  let kind = '';
  for (const guide of state.job.guides) {
    if (guide.axis === 'x' && Math.abs(world[0] - guide.at) < reach) { x = guide.at; kind = 'guide'; }
    if (guide.axis === 'y' && Math.abs(world[1] - guide.at) < reach) { y = guide.at; kind = 'guide'; }
  }
  if (state.snap.grid && state.snap.step > 0) {
    const step = state.snap.step;
    if (kind !== 'guide' || x === world[0]) x = Math.round(x / step) * step;
    if (kind !== 'guide' || y === world[1]) y = Math.round(y / step) * step;
    kind = kind || 'grid';
  }
  return { point: [x, y], kind };
}

function snapped(world, ignore = null) {
  const result = snapPoint(world, ignore);
  state.snapMark = result.kind && result.kind !== 'grid' ? result : null;
  return result.point;
}

function snapDelta(box, dx, dy, ignore) {
  const probes = [
    [box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]],
    [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2],
  ];
  let best = null, bestShift = Infinity;
  for (const [x, y] of probes) {
    const moved = [x + dx, y + dy];
    const result = snapPoint(moved, ignore);
    if (!result.kind) continue;
    const shift = Math.hypot(result.point[0] - moved[0], result.point[1] - moved[1]);
    if (shift < bestShift) {
      bestShift = shift;
      best = { ...result, shift: [result.point[0] - moved[0], result.point[1] - moved[1]] };
    }
  }
  state.snapMark = best && best.kind !== 'grid' ? best : null;
  return best ? [dx + best.shift[0], dy + best.shift[1]] : [dx, dy];
}

function penClick(world) {
  const pen = state.pen || { tool: state.tool, points: [] };
  state.pen = pen;
  const first = pen.points[0];
  const closing = first && pen.points.length > 2
    && Math.hypot(world[0] - first[0], world[1] - first[1]) * state.view.scale < 9;
  if (closing && (pen.tool === 'polyline' || pen.tool === 'curve')) {
    finishPen(true);
    return;
  }
  pen.points.push(world);
  if (pen.tool === 'line' && pen.points.length === 2) finishPen(false);
  else if (pen.tool === 'arc' && pen.points.length === 3) finishPen(false);
  draw();
}

async function finishPen(closed) {
  const pen = state.pen;
  state.pen = null;
  if (!pen || pen.points.length < 2) {
    draw();
    return;
  }
  const kind = { line: 'polyline', polyline: 'polyline', curve: 'curve', arc: 'arc' }[pen.tool];
  let points = pen.points;
  if (pen.tool === 'arc') points = [pen.points[0], pen.points[2], pen.points[1]];
  try {
    const { vectors } = await call('/api/shape', { kind, params: { points, closed } });
    addVectors(vectors);
  } catch (error) {
    say(error.message, true);
  }
  draw();
}

function drawPen() {
  const pen = state.pen;
  if (!pen) return;
  const { scale } = state.view;
  ctx.strokeStyle = css('--accent');
  ctx.lineWidth = 1.2 / scale;
  ctx.setLineDash([5 / scale, 4 / scale]);
  ctx.beginPath();
  pen.points.forEach(([x, y], index) => (index ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  if (state.pointer) ctx.lineTo(state.pointer[0], state.pointer[1]);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = css('--accent');
  const size = 6 / scale;
  for (const [x, y] of pen.points) ctx.fillRect(x - size / 2, y - size / 2, size, size);
}

function nodeVector() {
  return state.job.vectors.find((vector) => vector.id === state.nodeId) || null;
}

function drawNodes() {
  const vector = nodeVector();
  if (!vector) return;
  const { scale } = state.view;
  const size = 7 / scale;
  const nodes = vectorNodes(vector);
  if (vector.anchors) {
    ctx.strokeStyle = css('--muted');
    ctx.lineWidth = 0.8 / scale;
    ctx.beginPath();
    nodes.forEach(([x, y], index) => (index ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.stroke();
  }
  nodes.forEach(([x, y], index) => {
    ctx.fillStyle = index === state.nodeIndex ? css('--accent') : css('--surface');
    ctx.strokeStyle = css('--accent');
    ctx.lineWidth = 1.2 / scale;
    ctx.fillRect(x - size / 2, y - size / 2, size, size);
    ctx.strokeRect(x - size / 2, y - size / 2, size, size);
  });
}

function nodeDown(world, event) {
  const vector = nodeVector();
  if (vector) {
    const reach = 9 / state.view.scale;
    const nodes = vectorNodes(vector);
    for (let i = 0; i < nodes.length; i++) {
      if (Math.hypot(world[0] - nodes[i][0], world[1] - nodes[i][1]) < reach) {
        state.nodeIndex = i;
        state.gesture = { type: 'node', moved: false };
        draw();
        return;
      }
    }
    if (event.detail >= 2) {
      insertNode(vector, world);
      return;
    }
  }
  const hit = pick(world[0], world[1]);
  state.nodeId = hit ? hit.id : null;
  state.nodeIndex = -1;
  state.selection = hit ? new Set([hit.id]) : new Set();
  renderDesign();
  draw();
}

function insertNode(vector, world) {
  const nodes = vectorNodes(vector);
  let best = -1, bestDistance = 9 / state.view.scale;
  const spans = nodes.length - 1 + (vector.closed ? 1 : 0);
  for (let i = 0; i < spans; i++) {
    const a = nodes[i], c = nodes[(i + 1) % nodes.length];
    const distance = distanceToSegment(world[0], world[1], a[0], a[1], c[0], c[1]);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = i;
    }
  }
  if (best < 0) return;
  remember();
  nodes.splice(best + 1, 0, [...world]);
  state.nodeIndex = best + 1;
  refreshCurve(vector);
}

async function refreshCurve(vector) {
  if (vector.anchors) {
    try {
      const { vectors } = await call('/api/shape', {
        kind: 'curve',
        params: { points: vector.anchors, closed: vector.closed },
      });
      vector.points = vectors[0].points;
    } catch (error) {
      say(error.message, true);
    }
  }
  changed();
}

function removeNode() {
  const vector = nodeVector();
  if (!vector || state.nodeIndex < 0) return false;
  const nodes = vectorNodes(vector);
  if (nodes.length <= (vector.closed ? 3 : 2)) {
    say('A vector needs more points than that.', true);
    return true;
  }
  remember();
  nodes.splice(state.nodeIndex, 1);
  state.nodeIndex = -1;
  refreshCurve(vector);
  return true;
}

async function trimClick(world) {
  const hit = pick(world[0], world[1]);
  if (!hit) return;
  const others = state.job.vectors.filter((vector) => vector.id !== hit.id && shownVector(vector));
  try {
    const { vectors } = await call('/api/trim', { target: hit, cutters: others, x: world[0], y: world[1] });
    replaceVectors([hit.id], vectors);
  } catch (error) {
    say(error.message, true);
  }
}

async function extendClick(world) {
  const hit = pick(world[0], world[1]);
  if (!hit) return;
  const others = state.job.vectors.filter((vector) => vector.id !== hit.id && shownVector(vector));
  try {
    const { vectors } = await call('/api/extend', { target: hit, cutters: others, x: world[0], y: world[1] });
    remember();
    hit.points = vectors[0].points;
    hit.anchors = null;
    changed();
  } catch (error) {
    say(error.message, true);
  }
}

function measureClick(world) {
  if (!state.measure) {
    state.measure = world;
    draw();
    return;
  }
  remember();
  state.job.dimensions.push({ a: state.measure, b: world });
  state.measure = null;
  changed();
  renderDimensions();
}

function drawGuides() {
  const { width, height } = state.job.material;
  const { scale } = state.view;
  ctx.strokeStyle = '#2d7dd2';
  ctx.lineWidth = 1 / scale;
  ctx.setLineDash([6 / scale, 4 / scale]);
  const left = worldX(0), right = worldX(canvas.clientWidth);
  const top = worldY(0), bottom = worldY(canvas.clientHeight);
  for (const guide of state.job.guides) {
    ctx.beginPath();
    if (guide.axis === 'x') {
      ctx.moveTo(guide.at, bottom);
      ctx.lineTo(guide.at, top);
    } else {
      ctx.moveTo(left, guide.at);
      ctx.lineTo(right, guide.at);
    }
    ctx.stroke();
  }
  ctx.setLineDash([]);
  const tile = state.job.tiling;
  if (tile && tile.enabled && tile.width > 0 && tile.height > 0) {
    const stepX = tile.width - (tile.overlap || 0), stepY = tile.height - (tile.overlap || 0);
    ctx.strokeStyle = '#8e44ad';
    ctx.lineWidth = 1 / scale;
    ctx.setLineDash([9 / scale, 5 / scale]);
    for (let x = stepX; x < width && stepX > 0; x += stepX) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
    }
    for (let y = stepY; y < height && stepY > 0; y += stepY) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }
    ctx.setLineDash([]);
  }
}

function drawDimensions(ratio) {
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.font = '12px -apple-system, Helvetica, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'bottom';
  const list = [...state.job.dimensions];
  if (state.measure && state.pointer) list.push({ a: state.measure, b: state.pointer });
  for (const { a, b } of list) {
    const ax = screenX(a[0]), ay = screenY(a[1]), bx = screenX(b[0]), by = screenY(b[1]);
    ctx.strokeStyle = '#1f7a8c';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
    ctx.stroke();
    ctx.fillStyle = '#1f7a8c';
    const angle = Math.atan2(by - ay, bx - ax);
    for (const [x, y, turn] of [[ax, ay, angle], [bx, by, angle + Math.PI]]) {
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x + 9 * Math.cos(turn - 0.3), y + 9 * Math.sin(turn - 0.3));
      ctx.lineTo(x + 9 * Math.cos(turn + 0.3), y + 9 * Math.sin(turn + 0.3));
      ctx.fill();
    }
    const label = `${trim(toUnits(Math.hypot(b[0] - a[0], b[1] - a[1])))} ${unitLabel()}`;
    const middleX = (ax + bx) / 2, middleY = (ay + by) / 2;
    const width = ctx.measureText(label).width + 8;
    ctx.fillStyle = css('--surface');
    ctx.fillRect(middleX - width / 2, middleY - 17, width, 16);
    ctx.fillStyle = css('--text');
    ctx.fillText(label, middleX, middleY - 3);
  }
  if (state.snapMark && (state.gesture || state.pen || state.tool !== 'select')) {
    const [x, y] = state.snapMark.point;
    ctx.strokeStyle = '#2d7dd2';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(screenX(x) - 6, screenY(y) - 6, 12, 12);
  }
  if (state.issueMark) {
    ctx.strokeStyle = css('--danger');
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(screenX(state.issueMark[0]), screenY(state.issueMark[1]), 11, 0, Math.PI * 2);
    ctx.stroke();
  }
}

function rulerStep() {
  const perUnit = state.view.scale * (inches() ? MM : 1);
  for (const base of [0.01, 0.1, 1, 10, 100, 1000]) {
    for (const factor of [1, 2, 5]) {
      if (base * factor * perUnit >= 56) return base * factor;
    }
  }
  return 10000;
}

function drawRulers(ratio) {
  if (!state.rulers) return;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const w = canvas.clientWidth, h = canvas.clientHeight;
  ctx.fillStyle = css('--surface');
  ctx.fillRect(0, 0, w, RULER);
  ctx.fillRect(0, 0, RULER, h);
  ctx.strokeStyle = css('--line');
  ctx.lineWidth = 1;
  ctx.strokeRect(0.5, 0.5, w - 1, RULER - 1);
  ctx.strokeRect(0.5, 0.5, RULER - 1, h - 1);
  const step = rulerStep();
  const unit = inches() ? MM : 1;
  ctx.fillStyle = css('--muted');
  ctx.strokeStyle = css('--muted');
  ctx.font = '10px -apple-system, Helvetica, sans-serif';
  ctx.textBaseline = 'alphabetic';
  ctx.textAlign = 'left';
  const label = (value) => String(Number(value.toFixed(3)));
  const startX = Math.floor(worldX(RULER) / unit / step) * step;
  for (let value = startX; screenX(value * unit) < w; value += step) {
    for (let k = 0; k < 5; k++) {
      const x = screenX((value + (k * step) / 5) * unit);
      if (x < RULER) continue;
      ctx.beginPath();
      ctx.moveTo(x, RULER);
      ctx.lineTo(x, k ? RULER - 4 : RULER - 9);
      ctx.stroke();
    }
    const x = screenX(value * unit);
    if (x >= RULER) ctx.fillText(label(value), x + 2, 10);
  }
  const startY = Math.floor(worldY(h) / unit / step) * step;
  for (let value = startY; screenY(value * unit) > 0; value += step) {
    for (let k = 0; k < 5; k++) {
      const y = screenY((value + (k * step) / 5) * unit);
      if (y < RULER) continue;
      ctx.beginPath();
      ctx.moveTo(RULER, y);
      ctx.lineTo(k ? RULER - 4 : RULER - 9, y);
      ctx.stroke();
    }
    const y = screenY(value * unit);
    if (y >= RULER) {
      ctx.save();
      ctx.translate(9, y - 2);
      ctx.rotate(-Math.PI / 2);
      ctx.fillText(label(value), 0, 0);
      ctx.restore();
    }
  }
  ctx.fillStyle = css('--surface');
  ctx.fillRect(0, 0, RULER, RULER);
}

function guideAt(sx, sy) {
  for (const guide of state.job.guides) {
    const distance = guide.axis === 'x' ? Math.abs(screenX(guide.at) - sx) : Math.abs(screenY(guide.at) - sy);
    if (distance < 5) return guide;
  }
  return null;
}

function rulerDown(sx, sy) {
  if (!state.rulers || (sx >= RULER && sy >= RULER)) return false;
  remember();
  const axis = sy < RULER ? 'y' : 'x';
  const guide = { axis, at: axis === 'x' ? worldX(sx) : worldY(sy) };
  state.job.guides.push(guide);
  state.gesture = { type: 'guide', guide, sx, sy };
  return true;
}

function guideDrag(gesture, sx, sy, world) {
  const point = snapPoint(world, null).point;
  gesture.guide.at = gesture.guide.axis === 'x' ? point[0] : point[1];
  gesture.sx = sx;
  gesture.sy = sy;
}

function guideUp(gesture) {
  const gone = state.rulers && (gesture.guide.axis === 'x' ? gesture.sx < RULER : gesture.sy < RULER);
  if (gone) state.job.guides = state.job.guides.filter((guide) => guide !== gesture.guide);
  changed();
}
