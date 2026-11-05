'use strict';

const TOOL_KINDS = [['endmill', 'End mill'], ['ballnose', 'Ball nose'], ['vbit', 'V-bit'], ['drill', 'Drill']];

function replaceVectors(oldIds, vectors) {
  remember();
  const gone = new Set(oldIds);
  const fresh = vectors.map((vector) => vector.id);
  const kept = new Set(fresh);
  const layer = state.job.vectors.find((vector) => gone.has(vector.id));
  state.job.vectors = state.job.vectors.filter((vector) => !gone.has(vector.id));
  for (const vector of vectors) {
    if (layer && vector.layer === 'Layer 1') vector.layer = layer.layer;
  }
  state.job.vectors.push(...vectors);
  for (const op of state.job.operations) {
    if (op.vector_ids.some((id) => gone.has(id))) {
      const left = op.vector_ids.filter((id) => !gone.has(id) && !kept.has(id));
      op.vector_ids = [...left, ...fresh];
    }
  }
  state.selection = new Set(fresh);
  changed();
}

function chosenVectors() {
  return state.job.vectors.filter((vector) => state.selection.has(vector.id));
}

function referenceVector() {
  return state.job.vectors.find((vector) => vector.id === state.refId) || null;
}

function setReference() {
  const [first] = chosenVectors();
  if (!first) {
    say('Select one vector first.', true);
    return;
  }
  state.refId = first.id;
  renderReference();
}

function renderReference() {
  const vector = referenceVector();
  $('#ref-status').textContent = vector
    ? `Path set: ${vector.closed ? 'closed' : 'open'} vector with ${vector.points.length} points.`
    : 'No path set. Select a vector and press Set as path.';
}

function renderLayers() {
  const quick = $('#quick-layer');
  quick.replaceChildren(...state.job.layers.map((layer) => new Option(layer.name, layer.name)));
  quick.value = state.activeLayer;
  const list = $('#layer-list');
  list.replaceChildren();
  for (const layer of state.job.layers) {
    const row = document.createElement('li');
    row.className = layer.name === state.activeLayer ? 'active' : '';
    const pickButton = document.createElement('button');
    pickButton.textContent = layer.name;
    pickButton.title = 'Draw on this layer';
    pickButton.className = 'layer-name';
    pickButton.addEventListener('click', () => {
      state.activeLayer = layer.name;
      renderLayers();
    });
    row.append(pickButton);
    for (const [key, text, title] of [
      ['visible', 'Show', 'Show this layer'],
      ['locked', 'Lock', 'Lock this layer'],
      ['keepout', 'Keep-out', 'Treat closed vectors on this layer as keep-out zones'],
    ]) {
      const label = document.createElement('label');
      label.title = title;
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = Boolean(layer[key]);
      box.addEventListener('change', () => {
        remember();
        layer[key] = box.checked;
        changed();
      });
      label.append(box, document.createTextNode(` ${text}`));
      row.append(label);
    }
    list.append(row);
  }
}

function addLayer() {
  const name = $('#layer-name').value.trim();
  if (!name || layerOf(name)) {
    say('Enter a new layer name.', true);
    return;
  }
  remember();
  state.job.layers.push({ name, visible: true, locked: false, keepout: false });
  state.activeLayer = name;
  $('#layer-name').value = '';
  renderLayers();
  changed();
}

function moveToLayer() {
  if (!state.selection.size) {
    say('Select vectors first.', true);
    return;
  }
  remember();
  for (const vector of chosenVectors()) vector.layer = state.activeLayer;
  changed();
}

function removeLayer() {
  if (state.job.layers.length < 2) {
    say('A job needs one layer.', true);
    return;
  }
  if (state.job.vectors.some((vector) => vector.layer === state.activeLayer)) {
    say('Move or delete the vectors on that layer first.', true);
    return;
  }
  remember();
  state.job.layers = state.job.layers.filter((layer) => layer.name !== state.activeLayer);
  state.activeLayer = state.job.layers[0].name;
  renderLayers();
  changed();
}

function selectLayer() {
  state.selection = new Set(state.job.vectors.filter((vector) => vector.layer === state.activeLayer).map((v) => v.id));
  renderDesign();
  draw();
}

async function runLayout(body, message) {
  try {
    const { vectors } = await call('/api/layout', body);
    addVectors(vectors);
    say(message(vectors));
  } catch (error) {
    say(error.message, true);
  }
}

function gridArray() {
  const chosen = chosenVectors();
  if (!chosen.length) return;
  runLayout({
    kind: 'grid',
    vectors: chosen,
    columns: Number($('#grid-x').value),
    rows: Number($('#grid-y').value),
    step_x: toMm(Number($('#grid-step-x').value)),
    step_y: toMm(Number($('#grid-step-y').value)),
  }, (rows) => `Added ${rows.length} vectors.`);
}

function circularArray() {
  const chosen = chosenVectors();
  if (!chosen.length) return;
  runLayout({
    kind: 'circle',
    vectors: chosen,
    count: Number($('#circle-count').value),
    cx: toMm(Number($('#circle-x').value)),
    cy: toMm(Number($('#circle-y').value)),
    sweep: Number($('#circle-sweep').value),
    rotate: $('#circle-rotate').checked,
  }, (rows) => `Added ${rows.length} vectors.`);
}

function pathArray() {
  const chosen = chosenVectors().filter((vector) => vector.id !== state.refId);
  const path = referenceVector();
  if (!chosen.length || !path) {
    say('Set a path, then select the art to copy.', true);
    return;
  }
  runLayout({
    kind: 'path',
    vectors: chosen,
    path,
    count: Number($('#path-count').value),
    rotate: $('#circle-rotate').checked,
  }, (rows) => `Added ${rows.length} vectors along the path.`);
}

async function nestSelection() {
  const chosen = chosenVectors();
  if (!chosen.length) {
    say('Select the parts to nest.', true);
    return;
  }
  try {
    say('Nesting parts');
    const zones = state.job.vectors.filter((vector) => vector.closed && layerOf(vector.layer)?.keepout);
    const result = await call('/api/nest', {
      vectors: chosen.filter((vector) => !layerOf(vector.layer)?.keepout),
      keepouts: zones,
      width: state.job.material.width,
      height: state.job.material.height,
      gap: toMm(Number($('#nest-gap').value)),
      margin: toMm(Number($('#nest-margin').value)),
      rotation_step: Number($('#nest-rotate').value),
    });
    remember();
    const moved = new Map(result.vectors.map((vector) => [vector.id, vector]));
    for (const vector of state.job.vectors) {
      const row = moved.get(vector.id);
      if (row) {
        vector.points = row.points;
        vector.anchors = row.anchors;
      }
    }
    changed();
    const left = result.parts - result.placed;
    say(`Placed ${result.placed} of ${result.parts} parts. Sheet use ${result.used_percent.toFixed(0)} percent.${left ? ` ${left} did not fit and did not move.` : ''}`, left > 0);
  } catch (error) {
    say(error.message, true);
  }
}

async function checkVectors() {
  const rows = state.selection.size ? chosenVectors() : state.job.vectors.filter(shownVector);
  const list = $('#issue-list');
  list.replaceChildren();
  state.issueMark = null;
  try {
    const { issues } = await call('/api/validate', { vectors: rows });
    for (const issue of issues) {
      const item = document.createElement('li');
      item.textContent = issue.message;
      item.addEventListener('click', () => {
        state.selection = new Set(issue.vector_ids);
        state.issueMark = issue.point;
        renderDesign();
        draw();
      });
      list.append(item);
    }
    say(issues.length ? `Found ${issues.length} item${issues.length === 1 ? '' : 's'} to review.` : 'Vectors look clean.');
  } catch (error) {
    say(error.message, true);
  }
  draw();
}

async function exportVectors(format) {
  const rows = state.selection.size ? chosenVectors() : state.job.vectors.filter(shownVector);
  if (!rows.length) {
    say('There are no vectors to export.', true);
    return;
  }
  try {
    const file = await call('/api/export-vectors', {
      format,
      vectors: rows,
      width: state.job.material.width,
      height: state.job.material.height,
      title: $('#job-title').value || 'job',
    });
    platform.saveTextFile(file.name, file.text);
  } catch (error) {
    say(error.message, true);
  }
}

function renderDimensions() {
  const list = $('#dimension-list');
  list.replaceChildren();
  state.job.dimensions.forEach((dimension, index) => {
    const item = document.createElement('li');
    const length = Math.hypot(dimension.b[0] - dimension.a[0], dimension.b[1] - dimension.a[1]);
    item.textContent = `${trim(toUnits(length))} ${unitLabel()}`;
    const remove = document.createElement('button');
    remove.textContent = 'Remove';
    remove.addEventListener('click', () => {
      remember();
      state.job.dimensions.splice(index, 1);
      renderDimensions();
      changed();
    });
    item.append(remove);
    list.append(item);
  });
}

function selectedTextGroup() {
  const groups = new Set(chosenVectors().map((vector) => vector.group));
  if (groups.size !== 1) return null;
  const [group] = groups;
  return group && state.job.texts[group] ? group : null;
}

function dropTextRecords() {
  for (const vector of chosenVectors()) {
    if (vector.group) delete state.job.texts[vector.group];
  }
}

function renderKerning() {
  const host = $('#kern-tools');
  const group = selectedTextGroup();
  host.hidden = !group;
  if (!group) return;
  const record = state.job.texts[group];
  const list = $('#kern-list');
  list.replaceChildren();
  const step = inches() ? MM / 64 : 0.5;
  [...record.text].forEach((letter, index) => {
    if (letter === '\n' || index === record.text.length - 1) return;
    const item = document.createElement('div');
    item.className = 'kern';
    const label = document.createElement('span');
    label.textContent = `${letter} ${record.text[index + 1]}`;
    const tighter = document.createElement('button');
    tighter.textContent = '-';
    tighter.title = 'Move closer';
    const wider = document.createElement('button');
    wider.textContent = '+';
    wider.title = 'Move apart';
    tighter.addEventListener('click', () => nudgeKern(group, index, -step));
    wider.addEventListener('click', () => nudgeKern(group, index, step));
    item.append(label, tighter, wider);
    list.append(item);
  });
}

async function nudgeKern(group, index, change) {
  const record = state.job.texts[group];
  record.kerns = record.kerns || {};
  record.kerns[index] = (record.kerns[index] || 0) + change;
  await rebuildText(group);
}

async function rebuildText(group) {
  const record = state.job.texts[group];
  const old = state.job.vectors.filter((vector) => vector.group === group);
  const before = boxOf(old);
  try {
    const { vectors, warning } = await call('/api/text', record);
    if (!record.path) {
      const after = boxOf(vectors);
      for (const vector of vectors) {
        vector.points = vector.points.map(([x, y]) => [x + before[0] - after[0], y + before[1] - after[1]]);
      }
    }
    const layer = old[0].layer;
    for (const vector of vectors) {
      vector.group = group;
      vector.layer = layer;
    }
    replaceVectors(old.map((vector) => vector.id), vectors);
    if (warning) say(warning, true);
    renderKerning();
  } catch (error) {
    say(error.message, true);
  }
}

function boxOf(vectors) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const vector of vectors) {
    for (const [x, y] of vector.points) {
      x0 = Math.min(x0, x); y0 = Math.min(y0, y);
      x1 = Math.max(x1, x); y1 = Math.max(y1, y);
    }
  }
  return [x0, y0, x1, y1];
}

function textRequest() {
  return {
    text: $('#text-value').value,
    font: $('#text-font').value,
    height: toMm(Number($('#text-height').value)),
    align: $('#text-align').value,
    tracking: toMm(Number($('#text-tracking').value)),
    x: state.job.material.width / 2,
    y: state.job.material.height / 2,
    kerns: {},
  };
}

async function placeText(record) {
  try {
    const { vectors, warning } = await call('/api/text', record);
    const group = newId();
    state.job.texts[group] = record;
    addVectors(vectors, group);
    setTool('select');
    renderKerning();
    if (warning) say(warning, true);
  } catch (error) {
    say(error.message, true);
  }
}

function addText() {
  placeText(textRequest());
}

function addTextOnPath() {
  const path = referenceVector();
  if (!path) {
    say('Set a path first.', true);
    return;
  }
  placeText({
    ...textRequest(),
    path,
    raise_by: toMm(Number($('#text-raise').value)),
    flip: $('#text-flip').checked,
  });
}

async function mergeLibraryPost(row) {
  state.library.posts = state.library.posts.filter((post) => post.id !== row.id);
  state.library.posts.push(row);
  saveLibrary();
  syncPosts();
}

function saveLibrary() {
  platform.writeLibrary(JSON.stringify(state.library));
}

function syncPosts() {
  for (const row of state.library.posts) {
    if (!state.job.custom_posts.some((post) => post.id === row.id)) state.job.custom_posts.push(row);
  }
  const select = $('#post-select');
  const current = select.value;
  select.replaceChildren();
  for (const post of state.boot.posts) select.add(new Option(post.name, post.id));
  for (const post of state.job.custom_posts) select.add(new Option(post.name, post.id));
  select.value = [...select.options].some((option) => option.value === current) ? current : select.options[0].value;
}

function mergeTools(rows) {
  const known = new Map(state.job.tools.map((tool) => [tool.id, tool]));
  for (const row of rows) known.set(row.id, row);
  state.job.tools = [...known.values()];
  state.library.tools = [...new Map([...state.library.tools, ...rows].map((tool) => [tool.id, tool])).values()];
  saveLibrary();
}

async function importMachine() {
  const file = await platform.openBinaryFile('.pp,.vtdb');
  if (!file) return;
  try {
    say(`Reading ${file.name}`);
    const result = await call('/api/machine', { name: file.name, data: file.base64 });
    if (result.kind === 'post') {
      const row = { id: `pp-${newId()}`, name: result.name, source: result.source };
      state.job.custom_posts.push(row);
      await mergeLibraryPost(row);
      $('#post-select').value = row.id;
      scheduleCompute();
      openPosts(row.id, result);
      return;
    }
    mergeTools(result.tools);
    state.library.machines = result.machines;
    saveLibrary();
    if (result.machines.length) setMachine(result.machines[0]);
    renderMachines();
    renderForm();
    changed();
    const note = $('#tool-note');
    note.hidden = false;
    note.textContent = [`Imported ${result.tools.length} tools. Check feeds and speeds before you cut.`, ...result.notes].join(' ');
    openTools();
  } catch (error) {
    say(error.message, true);
  }
}

function setMachine(machine) {
  state.job.machine = machine || {};
  const tile = state.job.tiling;
  if (machine && machine.width > 0) {
    tile.width = tile.width || machine.width;
    tile.height = tile.height || machine.height;
  }
}

function renderMachines() {
  const select = $('#job-machine');
  select.replaceChildren(new Option('None set', ''));
  const rows = [...state.library.machines];
  if (state.job.machine.name && !rows.some((row) => row.name === state.job.machine.name)) rows.push(state.job.machine);
  for (const row of rows) select.add(new Option(row.name, row.name));
  select.value = state.job.machine.name || '';
  const hint = $('#machine-hint');
  const { width, height, controller } = state.job.machine;
  hint.textContent = width
    ? `Bed ${trim(toUnits(width))} x ${trim(toUnits(height))} ${unitLabel()}${controller ? `, ${controller} controller` : ''}.`
    : 'Import a Vectric .pp post or .vtdb tool database to load a machine.';
}

function filteredTools() {
  const { material, machine } = state.filter;
  return state.job.tools.filter((tool) => (!material || !tool.material || tool.material === material)
    && (!machine || !tool.machine || tool.machine === machine));
}

function renderToolFilters() {
  const fill = (select, values, current) => {
    select.replaceChildren(new Option('All', ''));
    for (const value of values) select.add(new Option(value, value));
    select.value = current;
  };
  const unique = (key) => [...new Set(state.job.tools.map((tool) => tool[key]).filter(Boolean))].sort();
  fill($('#filter-material'), unique('material'), state.filter.material);
  fill($('#filter-machine'), unique('machine'), state.filter.machine);
}

function renderToolList() {
  const list = $('#tool-list');
  list.replaceChildren();
  for (const tool of filteredTools()) {
    const item = document.createElement('li');
    item.className = tool.id === state.toolEdit ? 'active' : '';
    item.textContent = tool.name;
    const sub = document.createElement('span');
    sub.textContent = `${TOOL_KINDS.find(([kind]) => kind === tool.kind)?.[1] || tool.kind}, ${trim(toUnits(tool.diameter))} ${unitLabel()}${tool.material ? `, ${tool.material}` : ''}`;
    item.append(sub);
    item.addEventListener('click', () => {
      state.toolEdit = tool.id;
      renderToolList();
      renderToolForm();
    });
    list.append(item);
  }
}

function toolField(tool, key, text, kind = 'length') {
  const input = document.createElement('input');
  if (kind === 'text') {
    input.value = tool[key] ?? '';
    input.addEventListener('change', () => { tool[key] = input.value; toolEdited(); });
  } else {
    input.type = 'number';
    input.step = 'any';
    input.min = '0';
    const speed = kind === 'rate';
    const value = tool[key] ?? 0;
    input.value = speed || kind === 'length' ? trim(toUnits(value)) : value;
    input.addEventListener('change', () => {
      const number = Number(input.value) || 0;
      tool[key] = speed || kind === 'length' ? toMm(number) : number;
      toolEdited();
    });
  }
  const suffix = kind === 'length' ? ` (${unitLabel()})` : kind === 'rate' ? ` (${unitLabel()}/min)` : '';
  return labelled(`${text}${suffix}`, input);
}

function toolEdited() {
  const tool = state.job.tools.find((row) => row.id === state.toolEdit);
  if (tool && state.library.tools.some((row) => row.id === tool.id)) {
    state.library.tools = state.library.tools.map((row) => (row.id === tool.id ? tool : row));
    saveLibrary();
  }
  renderToolList();
  renderForm();
  changed();
}

function renderToolForm() {
  const host = $('#tool-form');
  host.replaceChildren();
  const tool = state.job.tools.find((row) => row.id === state.toolEdit);
  if (!tool) return;
  const grid = document.createElement('div');
  grid.className = 'grid';
  grid.append(toolField(tool, 'name', 'Name', 'text'));
  const kind = document.createElement('select');
  for (const [value, text] of TOOL_KINDS) kind.add(new Option(text, value));
  kind.value = tool.kind;
  kind.addEventListener('change', () => { tool.kind = kind.value; toolEdited(); renderToolForm(); });
  grid.append(labelled('Type', kind));
  grid.append(toolField(tool, 'diameter', 'Diameter'));
  if (tool.kind === 'vbit') {
    grid.append(toolField(tool, 'angle', 'Included angle (deg)', 'plain'));
    grid.append(toolField(tool, 'tip_diameter', 'Flat tip'));
  }
  grid.append(toolField(tool, 'feed', 'Feed', 'rate'));
  grid.append(toolField(tool, 'plunge', 'Plunge', 'rate'));
  grid.append(toolField(tool, 'rpm', 'Spindle RPM', 'plain'));
  grid.append(toolField(tool, 'stepdown', 'Stepdown'));
  grid.append(toolField(tool, 'stepover', 'Stepover'));
  grid.append(toolField(tool, 'flute_length', 'Flute length'));
  grid.append(toolField(tool, 'number', 'Tool number', 'plain'));
  grid.append(toolField(tool, 'material', 'Material', 'text'));
  grid.append(toolField(tool, 'machine', 'Machine', 'text'));
  host.append(grid);
  if (tool.notes) {
    const note = document.createElement('p');
    note.className = 'hint';
    note.textContent = tool.notes;
    host.append(note);
  }
}

function openTools() {
  renderToolFilters();
  renderToolList();
  renderToolForm();
  $('#tools-dialog').showModal();
}

function addTool() {
  const seed = state.job.tools.find((row) => row.id === state.toolEdit) || state.job.tools[0];
  const tool = { ...seed, id: `tool-${newId()}`, name: `${seed.name} copy` };
  state.job.tools.push(tool);
  state.library.tools.push(tool);
  saveLibrary();
  state.toolEdit = tool.id;
  renderToolList();
  renderToolForm();
  renderForm();
}

function removeTool() {
  const id = state.toolEdit;
  if (state.job.operations.some((op) => op.tool_id === id || op.params.clear_tool_id === id)) {
    say('A toolpath uses that tool.', true);
    return;
  }
  if (state.job.tools.length < 2) return;
  state.job.tools = state.job.tools.filter((tool) => tool.id !== id);
  state.library.tools = state.library.tools.filter((tool) => tool.id !== id);
  saveLibrary();
  state.toolEdit = state.job.tools[0].id;
  renderToolFilters();
  renderToolList();
  renderToolForm();
  renderForm();
}

function renderPostList() {
  const list = $('#post-list');
  list.replaceChildren();
  for (const post of state.job.custom_posts) {
    const item = document.createElement('li');
    item.className = post.id === state.postEdit ? 'active' : '';
    item.textContent = post.name;
    item.addEventListener('click', () => {
      state.postEdit = post.id;
      renderPostEditor();
    });
    list.append(item);
  }
}

function renderPostEditor(result = null) {
  renderPostList();
  const post = state.job.custom_posts.find((row) => row.id === state.postEdit);
  $('#post-editor').hidden = !post;
  if (!post) return;
  $('#post-source').value = post.source;
  showPostResult(result);
}

function showPostResult(result) {
  const host = $('#post-result');
  host.replaceChildren();
  if (!result) return;
  const report = { ...result.report, passed: result.report.passed };
  host.append(reportNode(`${result.name}, ${result.units === 'in' ? 'inches' : 'millimeters'}, .${result.extension}`, report));
  for (const note of result.notes) {
    const line = document.createElement('p');
    line.className = 'hint';
    line.textContent = note;
    host.append(line);
  }
  const sample = document.createElement('pre');
  sample.textContent = result.sample.split(/\r?\n/).slice(0, 24).join('\n');
  host.append(sample);
}

async function checkPost() {
  const post = state.job.custom_posts.find((row) => row.id === state.postEdit);
  if (!post) return;
  const source = $('#post-source').value;
  try {
    const result = await call('/api/post/check', { source });
    post.source = source;
    post.name = result.name;
    await mergeLibraryPost(post);
    renderPostList();
    showPostResult(result);
    changed();
    say(`Saved ${result.name}.`);
  } catch (error) {
    $('#post-result').textContent = error.message;
  }
}

function openPosts(id = null, result = null) {
  state.postEdit = id || state.postEdit || state.job.custom_posts[0]?.id || null;
  renderPostEditor(result);
  $('#posts-dialog').showModal();
}

function newPost() {
  const row = { id: `pp-${newId()}`, name: 'My machine (inch)(*.nc)', source: state.boot.starter_post };
  state.job.custom_posts.push(row);
  mergeLibraryPost(row);
  state.postEdit = row.id;
  renderPostEditor();
}

function removePost() {
  const id = state.postEdit;
  state.job.custom_posts = state.job.custom_posts.filter((post) => post.id !== id);
  state.library.posts = state.library.posts.filter((post) => post.id !== id);
  saveLibrary();
  syncPosts();
  state.postEdit = state.job.custom_posts[0]?.id || null;
  renderPostEditor();
  scheduleCompute();
}

function exportPost() {
  const post = state.job.custom_posts.find((row) => row.id === state.postEdit);
  if (post) platform.saveTextFile(`${post.name.replace(/[^A-Za-z0-9]+/g, '_')}.pp`, $('#post-source').value);
}

function checkedOperations() {
  return state.job.operations.filter((op) => state.checked.has(op.id));
}

function mergeChecked() {
  const chosen = checkedOperations();
  if (chosen.length < 2) {
    say('Tick two or more toolpaths to merge.', true);
    return;
  }
  if (new Set(chosen.map((op) => op.tool_id)).size > 1) {
    say('Merged toolpaths must use the same tool.', true);
    return;
  }
  remember();
  const merged = {
    kind: 'merge',
    tool_id: chosen[0].tool_id,
    vector_ids: chosen.flatMap((op) => op.vector_ids),
    params: { operations: chosen.map((op) => ({ ...op })) },
    name: 'Merged toolpath',
    id: newId(),
    enabled: true,
  };
  const at = state.job.operations.indexOf(chosen[0]);
  state.job.operations = state.job.operations.filter((op) => !state.checked.has(op.id));
  state.job.operations.splice(at, 0, merged);
  state.checked.clear();
  changed();
}

function splitMerged(op) {
  remember();
  const at = state.job.operations.indexOf(op);
  state.job.operations.splice(at, 1, ...op.params.operations);
  state.draft = null;
  renderForm();
  changed();
}

async function saveTemplate() {
  try {
    const ids = state.checked.size ? [...state.checked] : null;
    const template = await call('/api/template/make', { job: state.job, operation_ids: ids });
    platform.saveTextFile(`${$('#job-title').value || 'toolpaths'}.downcuttemplate`, JSON.stringify(template, null, 2));
  } catch (error) {
    say(error.message, true);
  }
}

async function applyTemplate() {
  const file = await platform.openTextFile('.downcuttemplate,.json');
  if (!file) return;
  try {
    const template = JSON.parse(file.text);
    const ids = state.selection.size ? [...state.selection] : null;
    const { operations } = await call('/api/template/apply', { job: state.job, template, vector_ids: ids });
    remember();
    state.job.operations.push(...operations);
    changed();
    say(`Added ${operations.length} toolpath${operations.length === 1 ? '' : 's'} from ${file.name}.`);
  } catch (error) {
    say(error.message, true);
  }
}

async function runInlay(kind, draft) {
  const ids = [...state.selection];
  if (!ids.length) {
    say('Select the closed vectors for the inlay first.', true);
    return;
  }
  try {
    const result = await call('/api/inlay', { job: state.job, kind, vector_ids: ids, tool_id: draft.tool_id, ...cleanParams(draft.params) });
    remember();
    state.job.layers.push(...result.layers);
    state.job.vectors.push(...result.vectors);
    state.job.operations.push(...result.operations);
    state.draft = null;
    renderForm();
    renderLayers();
    changed();
    say(result.notes.join(' ') || 'Added the pocket and the plug.', result.notes.length > 0);
  } catch (error) {
    say(error.message, true);
  }
}

async function openSheet() {
  if (!state.job.operations.length) {
    say('Create a toolpath first.', true);
    return;
  }
  const dialog = $('#sheet-dialog');
  const frame = $('#sheet-frame');
  frame.srcdoc = '';
  dialog.showModal();
  try {
    const sheet = await call('/api/sheet', { job: state.job, post: $('#post-select').value, title: $('#job-title').value || 'job' });
    frame.srcdoc = sheet.html;
    state.sheet = sheet;
  } catch (error) {
    frame.srcdoc = `<p>${error.message}</p>`;
  }
}
