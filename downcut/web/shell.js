'use strict';

const FORM_TITLES = {
  job: 'Job Setup',
  text: 'Text',
  transform: 'Move, Scale, Rotate',
  offset: 'Offset',
  clean: 'Join and Clean Up',
  corners: 'Fillet and Bones',
  path: 'Path and Boundary',
  arrays: 'Arrays',
  nest: 'Nesting',
  check: 'Check and Export',
};

function openDock(side) {
  const dock = $(`#dock-${side}`);
  dock.classList.remove('collapsed');
}

function openForm(name) {
  state.form = name;
  openDock('left');
  $('#drawing-home').hidden = true;
  $('#form-host').hidden = false;
  $('#form-title').textContent = FORM_TITLES[name];
  for (const section of $$('#form-host > [data-form]')) section.hidden = section.dataset.form !== name;
}

function closeForm() {
  state.form = null;
  $('#drawing-home').hidden = false;
  $('#form-host').hidden = true;
}

function setDock(name) {
  openDock('right');
  $$('.dock-tabs [data-dock]').forEach((button) => button.classList.toggle('active', button.dataset.dock === name));
  $('#tab-paths').hidden = name !== 'paths';
  $('#tab-layers').hidden = name !== 'layers';
}

function setLayout(mode) {
  $('#dock-left').classList.toggle('collapsed', mode === 'machine');
  $('#dock-right').classList.toggle('collapsed', mode === 'design');
  $$('.dock').forEach((dock) => dock.classList.remove('peek'));
  if (state.viewMode === '2d') {
    requestAnimationFrame(() => {
      resize();
      fit();
    });
  }
}

function setView(mode) {
  state.viewMode = mode;
  $('#stage-2d').hidden = mode !== '2d';
  $('#stage-3d').hidden = mode !== '3d';
  $('#options-2d').hidden = mode !== '2d';
  $('#options-3d').hidden = mode !== '3d';
  $('#view-2d').classList.toggle('active', mode === '2d');
  $('#view-3d').classList.toggle('active', mode === '3d');
  if (mode === '2d') {
    resize();
  } else if (state.surfaceStale || !state.surface) {
    runPreview();
  } else {
    showPreview();
  }
}

function closeMenus() {
  $$('.menu.open').forEach((menu) => menu.classList.remove('open'));
}

function bindMenus() {
  const menus = $$('.menu');
  for (const menu of menus) {
    const title = $('.menu-title', menu);
    title.addEventListener('click', (event) => {
      event.stopPropagation();
      const wasOpen = menu.classList.contains('open');
      closeMenus();
      if (!wasOpen) menu.classList.add('open');
    });
    title.addEventListener('mouseenter', () => {
      if ($('.menu.open') && !menu.classList.contains('open')) {
        closeMenus();
        menu.classList.add('open');
      }
    });
    $('.menu-list', menu).addEventListener('click', closeMenus);
  }
  document.addEventListener('click', closeMenus);
}

const RUN_ACTIONS = {
  'select-all': () => {
    state.selection = new Set(state.job.vectors.filter(pickableVector).map((vector) => vector.id));
    renderDesign();
    draw();
  },
  'view-2d': () => setView('2d'),
  'view-3d': () => setView('3d'),
  'layout-design': () => setLayout('design'),
  'layout-machine': () => setLayout('machine'),
  'layout-both': () => setLayout('both'),
  quit: async () => {
    await call('/api/shutdown', {});
    document.body.textContent = 'Downcut has stopped. You can close this tab.';
  },
  shortcuts: () => $('#shortcuts-dialog').showModal(),
  about: () => {
    $('#about-version').textContent = `Version ${state.boot.version}`;
    $('#about-dialog').showModal();
  },
};

function bindDock(side) {
  const dock = $(`#dock-${side}`);
  $(`#pin-${side}`).addEventListener('click', () => {
    dock.classList.add('collapsed');
    dock.classList.remove('peek');
  });
  $(`#unpin-${side}`).addEventListener('click', () => {
    dock.classList.remove('collapsed', 'peek');
  });
  dock.addEventListener('mouseenter', () => {
    if (dock.classList.contains('collapsed')) dock.classList.add('peek');
  });
  dock.addEventListener('mouseleave', () => {
    const busy = side === 'left' ? state.form : state.draft;
    if (!busy) dock.classList.remove('peek');
  });
}

function bindShell() {
  bindMenus();
  bindDock('left');
  bindDock('right');
  for (const button of $$('[data-click]')) {
    button.addEventListener('click', () => document.getElementById(button.dataset.click).click());
  }
  for (const button of $$('[data-run]')) {
    button.addEventListener('click', () => RUN_ACTIONS[button.dataset.run]());
  }
  for (const button of $$('button[data-form]')) {
    button.addEventListener('click', () => openForm(button.dataset.form));
  }
  $$('.dock-tabs [data-dock]').forEach((button) => button.addEventListener('click', () => setDock(button.dataset.dock)));
  $('#form-close').addEventListener('click', closeForm);
  $('#view-2d').addEventListener('click', () => setView('2d'));
  $('#view-3d').addEventListener('click', () => setView('3d'));
  $('#btn-recalc').addEventListener('click', compute);
  $('#safety-ok').addEventListener('click', () => {
    state.library.safetyAccepted = true;
    saveLibrary();
    $('#safety-dialog').close();
  });
  $('#quick-layer').addEventListener('change', (event) => {
    state.activeLayer = event.target.value;
    renderLayers();
  });
  new ResizeObserver(() => {
    if (state.viewMode === '2d') resize();
    else orbit.paint();
  }).observe($('.stage'));
}

function renderJobStatus() {
  const { width, height, thickness } = state.job.material;
  $('#status-job').textContent = `Job ${trim(toUnits(width))} x ${trim(toUnits(height))} x ${trim(toUnits(thickness))} ${unitLabel()}`;
}
