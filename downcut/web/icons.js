'use strict';

const TRASH = '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>';
const EXPORT = '<path d="M12 15V4M8 8l4-4 4 4"/><path d="M5 14v6h14v-6"/>';
const LAYERS = '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>';
const BOOKMARK = '<path d="M6 3h12v18l-6-4-6 4z"/>';

const ICONS = {
  'btn-new': '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M12 11v6M9 14h6"/>',
  'btn-open': '<path d="M3 19V5h6l2 3h10v11z"/>',
  'btn-save': '<path d="M4 4h13l3 3v13H4z"/><path d="M8 4v6h8V4"/><path d="M8 20v-6h8v6"/>',
  'btn-import': '<path d="M12 4v11M8 11l4 4 4-4"/><path d="M5 14v6h14v-6"/>',
  'btn-undo': '<path d="M8 5L3 10l5 5"/><path d="M3 10h11a6 6 0 0 1 0 12h-3"/>',
  'btn-redo': '<path d="M16 5l5 5-5 5"/><path d="M21 10H10a6 6 0 0 0 0 12h3"/>',
  'btn-preview': '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
  'btn-sheet': '<path d="M6 3h12v18H6z"/><path d="M9 8h6M9 12h6M9 16h4"/>',
  'btn-recalc': '<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 3v4h-4"/>',
  'btn-export': '<path d="M12 4v11M8 11l4 4 4-4"/><path d="M5 14v6h14v-6"/>',
  'open-job': '<path d="M3 8l9-5 9 5v8l-9 5-9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
  'open-transform': '<path d="M12 3v18M3 12h18"/><path d="M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/>',
  'open-offset': '<rect x="3" y="3" width="18" height="18" rx="2"/><rect x="8" y="8" width="8" height="8"/>',
  'open-clean': '<path d="M3 17c3 0 4-5 8-5"/><path d="M21 7c-3 0-4 5-7 5"/><circle cx="12.5" cy="12" r="1.5"/>',
  'open-corners': '<path d="M4 20V11a7 7 0 0 1 7-7h9"/>',
  'open-arrays': '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
  'open-path': '<path d="M3 20C9 19 15 13 20 4"/><rect x="5" y="14" width="4" height="4"/><rect x="12" y="8" width="4" height="4"/>',
  'open-nest': '<path d="M3 3h9v6H8v6H3z"/><path d="M21 21h-9v-6h4V9h5z"/>',
  'open-check': '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>',
  'op-rotate': '<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 3v4h-4"/>',
  'op-offset': '<rect x="3" y="3" width="18" height="18" rx="2"/><rect x="8" y="8" width="8" height="8"/>',
  'op-mirror-h': '<path d="M12 3v18"/><path d="M9 7L3 17h6z"/><path d="M15 7l6 10h-6z"/>',
  'op-mirror-v': '<path d="M3 12h18"/><path d="M7 9L17 3v6z"/><path d="M7 15l10 6v-6z"/>',
  'op-weld': '<path d="M12 5.7A7 7 0 1 1 12 18.3A7 7 0 1 1 12 5.7z"/>',
  'op-subtract': '<path d="M12 5.7A7 7 0 1 0 12 18.3A7 7 0 0 1 12 5.7z"/>',
  'op-intersect': '<path d="M12 5.7A7 7 0 0 1 12 18.3A7 7 0 0 1 12 5.7z"/>',
  'op-center': '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M12 8v8M8 12h8"/>',
  'op-duplicate': '<rect x="9" y="9" width="12" height="12" rx="1.5"/><path d="M15 9V4.5A1.5 1.5 0 0 0 13.5 3h-9A1.5 1.5 0 0 0 3 4.5v9A1.5 1.5 0 0 0 4.5 15H9"/>',
  'op-delete': TRASH,
  'op-join': '<path d="M3 17c3 0 4-5 8-5"/><path d="M21 7c-3 0-4 5-7 5"/><circle cx="12.5" cy="12" r="1.5"/>',
  'op-close': '<path d="M8 19L4 8l9-4 7 7-4 8"/><path d="M11 19h2"/>',
  'op-smooth': '<path d="M3 16c3-12 6-12 9-4s6 8 9-4"/>',
  'op-simplify': '<path d="M3 17l7-10 5 6 6-8"/>',
  'op-dedupe': '<rect x="3" y="3" width="12" height="12" rx="1.5"/><path d="M15 15l6 6M21 15l-6 6"/>',
  'op-fillet': '<path d="M4 20V11a7 7 0 0 1 7-7h9"/>',
  'op-dogbone': '<path d="M4 4v11M9 20h11"/><circle cx="6" cy="18" r="3"/>',
  'op-tbone': '<path d="M4 4v13M10 20h10"/><circle cx="7" cy="20" r="3"/>',
  'ref-set': '<path d="M3 18c4 0 5-12 9-12s5 12 9 12"/><circle cx="12" cy="6" r="2"/>',
  'path-add': '<path d="M3 20C9 19 15 13 20 4"/><rect x="5" y="14" width="4" height="4"/><rect x="12" y="8" width="4" height="4"/>',
  'op-boundary': '<rect x="6" y="5" width="14" height="14" stroke-dasharray="3 2"/><path d="M2 12h12"/>',
  'grid-add': '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
  'circle-add': '<circle cx="12" cy="4.5" r="2"/><circle cx="18.5" cy="8.2" r="2"/><circle cx="18.5" cy="15.8" r="2"/><circle cx="12" cy="19.5" r="2"/><circle cx="5.5" cy="15.8" r="2"/><circle cx="5.5" cy="8.2" r="2"/>',
  'nest-run': '<path d="M3 3h9v6H8v6H3z"/><path d="M21 21h-9v-6h4V9h5z"/>',
  'text-add': '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
  'text-path': '<path d="M3 20C7 8 17 8 21 20"/><path d="M8 4h8M12 4v8"/>',
  'check-run': '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>',
  'export-svg': EXPORT,
  'export-dxf': EXPORT,
  'layer-add': '<path d="M12 5v14M5 12h14"/>',
  'layer-move': LAYERS,
  'layer-select': '<rect x="4" y="4" width="16" height="16" stroke-dasharray="3 3"/>',
  'layer-remove': TRASH,
  'op-merge': '<path d="M5 4v6c0 4 7 3 7 8v2M19 4v6c0 4-7 3-7 8"/>',
  'template-save': BOOKMARK,
  'template-apply': `${BOOKMARK}<path d="M9 9l2.5 2.5L15 7"/>`,
  'btn-machine': '<path d="M12 4v11M8 11l4 4 4-4"/><path d="M5 14v6h14v-6"/>',
  'btn-tools': '<path d="M9 3h6v7l-3 11-3-11z"/>',
  'btn-posts': '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
  profile: '<rect x="8" y="8" width="8" height="8"/><rect x="3" y="3" width="18" height="18" rx="3" stroke-dasharray="3 2"/>',
  pocket: '<rect x="3" y="3" width="18" height="18" rx="1"/><path d="M7 8h10M7 12h10M7 16h10"/>',
  vcarve: '<path d="M4 5l8 15 8-15z"/>',
  drill: '<path d="M12 3v13M8 12l4 5 4-5"/><path d="M6 21h12"/>',
  chamfer: '<path d="M4 20V10l6-6h10"/>',
  fluting: '<path d="M6 4v16M12 4v16M18 4v16"/>',
  prism: '<path d="M3 18h18L12 5z"/><path d="M12 5v13"/>',
  auto_inlay: '<path d="M4 9v11h16V9"/><path d="M8 4h8v11H8z"/>',
  vcarve_inlay: '<path d="M4 20l8-9 8 9"/><path d="M4 3h16l-8 9z"/>',
};

function addIcon(button, name) {
  const body = ICONS[name];
  if (!body) return;
  if (button.closest('.tool-grid')) {
    const label = button.textContent.trim();
    button.title = button.title || label;
    button.setAttribute('aria-label', label);
    button.textContent = '';
  }
  button.classList.add('has-icon');
  button.insertAdjacentHTML('afterbegin', `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`);
}

function decorateButtons() {
  for (const name of Object.keys(ICONS)) {
    const button = document.getElementById(name);
    if (button) addIcon(button, name);
  }
}
