// The train editor: a full-width view over the map for composing a train. A drawing of the
// train to scale (direction of travel, leading cab, raised pantographs, axles, tail lamps;
// runs of identical vehicles folded) and a table with one column per vehicle group, the
// train's value and how it is found. The values of a column belong to its vehicle profile,
// so editing one changes every train that uses that vehicle.
import type { CompatCombined, CompatOver, CompatRule, CompatRules, TermValue, VehicleValues } from './api';
import { fmt, h, unitLabel } from './state';
import * as V from './vehicles';

export interface EditorCtx {
  rules: CompatRules;
  train: V.TrainProfile;
  profiles: V.VehicleProfile[];
  combined: CompatCombined | null;
  /** Label of a value (concept labels from the index). */
  label: (r: CompatRule, v: TermValue) => string;
  /** The train's groups, counts or roles changed. */
  changed: () => void;
  setVehicleValue: (profile: string, key: string, v: VehicleValues[string] | undefined) => void;
  setTrainValue: (key: string, v: VehicleValues[string] | undefined) => void;
  /** A new vehicle profile for the train (from the Add select). */
  newVehicle: () => string;
  close: () => void;
  showOnMap: () => void;
}

const DEFAULT_LENGTH = 20;   // m, a vehicle without a length in the drawing
const PX_PER_M = 5.5;
const FOLD = 3;              // a group with more vehicles than this is folded

let root: HTMLElement | null = null;
let ctx: EditorCtx | null = null;
let selected = -1;
let dragFrom = -1;
const openRows = new Set<string>();

export const isOpen = () => !!root?.isConnected;

/** Which vehicle groups count for a rule (as the API's _scope). */
export function inScope(over: CompatOver | undefined, groups: V.TrainGroup[], i: number): boolean {
  switch (over ?? 'all') {
    case 'leading': {
      const lead = groups.findIndex((g) => g.roles.includes('leading'));
      return i === (lead < 0 ? 0 : lead);
    }
    case 'tail': return i === groups.length - 1;
    case 'pantograph': return groups[i].roles.includes('pantograph');
    case 'train': return false;
    default: return true;
  }
}

const OUT_OF_SCOPE: Record<string, string> = {
  leading: 'not the leading cab', tail: 'not the tail', pantograph: 'no pantograph raised', train: 'train value only',
};

/** A value as text: number with unit, the option or concept labels, Yes / No. */
export function formatValue(r: CompatRule, v: VehicleValues[string] | undefined, label: EditorCtx['label']): string {
  if (!V.isFilled(v)) return '';
  if (typeof v === 'number') return `${fmt.format(v)}${r.unit ? ` ${unitLabel(r.unit)}` : ''}`;
  const xs = v as (string | boolean)[];
  if (xs.includes('urn:rinf-atlas:no-common-value')) return 'none in common';
  return xs.map((x) => r.options?.find((o) => o.value === x)?.label ?? label(r, x)).join(', ');
}

/** The rules with those of one group together, groups in the order they first appear. */
export function groupedRules(rules: CompatRule[]): CompatRule[] {
  const first = new Map<string, number>();
  rules.forEach((r, i) => { const g = r.group ?? ''; if (!first.has(g)) first.set(g, i); });
  return rules.map((r, i) => ({ r, i })).sort((a, b) => first.get(a.r.group ?? '')! - first.get(b.r.group ?? '')! || a.i - b.i).map((x) => x.r);
}

const profileOf = (id: string) => ctx!.profiles.find((p) => p.id === id);
const vehicleName = (id: string) => (profileOf(id)?.name ?? 'Missing vehicle profile').replace(/^Example: /, '');
const lengthOf = (id: string) => {
  const n = profileOf(id)?.values.trainLength;
  return typeof n === 'number' && n > 0 ? n : null;
};

// ---------- the drawing ----------
function vehicleBox(g: V.TrainGroup, k: number, isFront: boolean, isTail: boolean): HTMLElement {
  const len = lengthOf(g.profile);
  const w = Math.max(56, Math.round((len ?? DEFAULT_LENGTH) * PX_PER_M));
  const cab = isFront && g.roles.includes('leading');
  const panto = g.roles.includes('pantograph');
  return h('div', {
    class: 'tv' + (cab ? ' cab' : '') + (isTail ? ' tail' : '') + (len ? '' : ' no-length'), style: `width:${w}px`,
    title: `${vehicleName(g.profile)}${len ? `, ${fmt.format(len)} m` : ', no length: drawn as 20 m'}`,
  },
  panto ? h('span', { class: 'tv-panto', 'aria-hidden': 'true' }) : null,
  cab ? h('span', { class: 'tv-flag' }, 'Leading cab') : null,
  isTail ? h('span', { class: 'tv-flag tail' }, 'Tail') : null,
  h('span', { class: 'tv-name' }, `${k} · ${vehicleName(g.profile)}`),
  h('span', { class: 'tv-len' }, len ? `${fmt.format(len)} m` : 'length?'),
  h('span', { class: 'tv-axles', 'aria-hidden': 'true' }, h('i'), h('i'), h('i'), h('i')),
  isTail ? h('span', { class: 'tv-lamps', 'aria-hidden': 'true' }) : null);
}

function drawing(): HTMLElement {
  const t = ctx!.train;
  const strip = h('div', { class: 'train-strip', role: 'list', 'aria-label': 'Train, front to back' });
  let k = 1;
  t.groups.forEach((g, gi) => {
    const boxes: Node[] = [];
    const first = k;
    const isLastGroup = gi === t.groups.length - 1;
    const shown = g.count > FOLD ? [0, 1, g.count - 1] : [...Array(g.count).keys()];
    for (const j of shown) {
      if (g.count > FOLD && j === g.count - 1) {
        boxes.push(h('div', { class: 'tv fold' }, h('span', { class: 'tv-name' }, `… ${g.count - 3} more`),
          h('span', { class: 'tv-len' }, `same vehicle · ×${g.count} in all`)));
      }
      boxes.push(vehicleBox(g, first + j, j === 0, isLastGroup && j === g.count - 1));
    }
    k += g.count;
    const el = h('div', {
      class: 'train-group' + (gi === selected ? ' selected' : ''), role: 'listitem', draggable: 'true', tabindex: 0,
      'data-group': gi, 'aria-label': `Vehicles ${first}${g.count > 1 ? `–${first + g.count - 1}` : ''}: ${vehicleName(g.profile)}`,
      onclick: () => select(gi),
      onkeydown: (e: Event) => { if ((e as KeyboardEvent).key === 'Enter') select(gi); },
      ondragstart: (e: Event) => { dragFrom = gi; (e as DragEvent).dataTransfer?.setData('text/plain', String(gi)); },
      ondragover: (e: Event) => { e.preventDefault(); el.classList.add('drop'); },
      ondragleave: () => el.classList.remove('drop'),
      ondrop: (e: Event) => { e.preventDefault(); el.classList.remove('drop'); move(dragFrom, gi); },
    }, ...boxes);
    strip.append(el);
  });
  if (!t.groups.length) strip.append(h('p', { class: 'muted small' }, 'No vehicles yet. Add a vehicle profile below.'));
  const lead = t.groups.findIndex((g) => g.roles.includes('leading'));
  return h('div', { class: 'train-drawing' },
    h('div', { class: 'train-drawing-head' },
      h('span', { class: 'train-dir' }, '◀ Direction of travel'),
      h('span', { class: 'muted small' }, 'Drawn to scale. Drag a vehicle group to reorder it, click it to find its column below.'),
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn btn-small', type: 'button', title: 'Turn the train around: the tail becomes the front', onclick: reverse }, '⇄ Reverse')),
    h('div', { class: 'train-strip-wrap' }, strip),
    lead > 0 ? h('p', { class: 'small warn-text' }, 'The leading cab is not at the front of the train.') : null,
    summary());
}

function summary(): HTMLElement {
  const c = ctx!.combined;
  const r = (k: string) => ctx!.rules.rules.find((x) => x.key === k);
  const v = (k: string) => (c && r(k) ? formatValue(r(k)!, c.profile[k], ctx!.label) : '');
  const n = ctx!.train.groups.reduce((s, g) => s + g.count, 0);
  const pantos = ctx!.train.groups.filter((g) => g.roles.includes('pantograph')).reduce((s, g) => s + g.count, 0);
  const parts = [
    v('trainLength') ? `Train length ${v('trainLength')}` : null,
    `${fmt.format(n)} vehicle${n === 1 ? '' : 's'}`,
    pantos ? `pantographs raised: ${v('raisedPantographs') || fmt.format(pantos)}${v('pantographSpacing') ? `, ${v('pantographSpacing')} apart` : ''}` : null,
    v('axleLoad') ? `heaviest axle ${v('axleLoad')}` : null,
    v('maxSpeed') ? `speed ${v('maxSpeed')}` : null,
  ].filter(Boolean);
  return h('p', { class: 'train-summary small' }, parts.join(' · '));
}

function select(gi: number) {
  selected = gi;
  render();
  root?.querySelector<HTMLElement>(`th[data-group="${gi}"]`)?.scrollIntoView({ block: 'nearest', inline: 'center' });
}

function move(from: number, to: number) {
  const gs = ctx!.train.groups;
  if (from < 0 || to < 0 || from === to || from >= gs.length || to >= gs.length) return;
  const [g] = gs.splice(from, 1);
  gs.splice(to, 0, g);
  selected = to;
  ctx!.changed();
  render();
}

function reverse() {
  const gs = ctx!.train.groups;
  const lead = gs.find((g) => g.roles.includes('leading'));
  gs.reverse();
  // double-headed or a unit at each end: the cab at the new front leads
  if (lead && gs[0] !== lead && gs[0].profile === lead.profile) {
    lead.roles = lead.roles.filter((x) => x !== 'leading');
    gs[0].roles = ['leading', ...gs[0].roles.filter((x) => x !== 'leading')];
  }
  selected = -1;
  ctx!.changed();
  render();
}

// ---------- the table ----------
function cellEditor(r: CompatRule, cur: VehicleValues[string] | undefined, set: (v: VehicleValues[string] | undefined) => void, name: string): HTMLElement {
  if (r.kind === 'number') {
    const input = h('input', { type: 'number', inputmode: 'decimal', step: 'any', class: 'te-num', 'aria-label': name,
      value: typeof cur === 'number' ? String(cur) : '', placeholder: r.vehicle_default != null && !name.endsWith('whole train') ? `${fmt.format(r.vehicle_default)} if empty` : '—' });
    input.addEventListener('change', () => {
      const t = input.value.trim();
      const n = Number(t);
      set(t === '' || !Number.isFinite(n) ? undefined : n);
    });
    return h('span', { class: 'te-num-wrap' }, input, r.unit ? h('span', { class: 'muted small' }, unitLabel(r.unit)) : null);
  }
  const xs = (Array.isArray(cur) ? cur : []) as (string | boolean)[];
  if (r.options?.length || r.kind === 'boolean') {
    const choices: [string, string, string | boolean][] = r.options?.length
      ? r.options.map((o) => [o.value, o.label, o.value]) : [['true', 'Yes', true], ['false', 'No', false]];
    const sel = h('select', { class: 'te-select', 'aria-label': name },
      h('option', { value: '' }, '—'),
      ...choices.map(([k, l]) => h('option', { value: k, selected: xs.length === 1 && String(xs[0]) === k }, l)));
    if (xs.length > 1) sel.prepend(h('option', { value: '*', selected: true }, formatValue(r, xs, ctx!.label)));
    sel.addEventListener('change', () => {
      const c = choices.find(([k]) => k === sel.value);
      if (sel.value !== '*') set(c ? [c[2]] : undefined);
    });
    return sel;
  }
  // concepts: a summary that unfolds into checkboxes
  const vals = [...(r.values ?? []).map((v) => String(v.value)), ...xs.map(String)].filter((v, i, a) => a.indexOf(v) === i);
  const d = h('details', { class: 'te-terms' }, h('summary', {}, xs.length ? formatValue(r, xs, ctx!.label) : '—'),
    h('div', { class: 'te-terms-list' }, ...vals.map((v) => h('label', { class: 'check' },
      h('input', { type: 'checkbox', checked: xs.map(String).includes(v), onchange: (e: Event) => {
        const on = (e.target as HTMLInputElement).checked;
        const now = xs.filter((x) => String(x) !== v);
        set(on ? [...now, v] : now);
      } }),
      h('span', { class: 'lbl' }, v === r.none_value ? `${ctx!.label(r, v)} (none)` : v === r.without_value ? r.without_label ?? ctx!.label(r, v) : ctx!.label(r, v))))));
  if (openRows.has(`${r.key}:${name}`)) d.open = true;
  d.addEventListener('toggle', () => { d.open ? openRows.add(`${r.key}:${name}`) : openRows.delete(`${r.key}:${name}`); });
  return d;
}

function groupHead(g: V.TrainGroup, gi: number, first: number): HTMLElement {
  const t = ctx!.train;
  const label = `${first}${g.count > 1 ? `–${first + g.count - 1}` : ''}`;
  const count = h('input', { type: 'number', min: 1, max: 500, step: 1, class: 'te-count', value: String(g.count), 'aria-label': `Number of ${vehicleName(g.profile)}` });
  count.addEventListener('change', () => {
    const n = Math.round(Number(count.value));
    g.count = Number.isFinite(n) ? Math.min(500, Math.max(1, n)) : 1;
    ctx!.changed(); render();
  });
  const role = (r: V.Role, text: string, title: string) => h('label', { class: 'te-role', title },
    h('input', { type: 'checkbox', checked: g.roles.includes(r), onchange: (e: Event) => {
      const on = (e.target as HTMLInputElement).checked;
      if (r === 'leading' && on) for (const x of t.groups) x.roles = x.roles.filter((y) => y !== 'leading');
      g.roles = on ? [...g.roles.filter((y) => y !== r), r] : g.roles.filter((y) => y !== r);
      ctx!.changed(); render();
    } }), text);
  const prof = h('select', { class: 'te-select', 'aria-label': `Vehicle profile of vehicles ${label}`, onchange: (e: Event) => {
    g.profile = (e.target as HTMLSelectElement).value; ctx!.changed(); render();
  } }, ...(profileOf(g.profile) ? [] : [h('option', { value: g.profile, selected: true }, 'Missing profile')]),
  ...ctx!.profiles.map((p) => h('option', { value: p.id, selected: p.id === g.profile }, p.name)));
  const btn = (text: string, title: string, fn: () => void, disabled = false) =>
    h('button', { class: 'icon-btn', type: 'button', title, 'aria-label': title, disabled, onclick: fn }, text);
  return h('th', { class: 'te-group' + (gi === selected ? ' selected' : ''), 'data-group': gi, scope: 'col' },
    h('div', { class: 'te-group-name' }, h('span', {}, `${label} · `), prof),
    h('div', { class: 'te-group-row' }, h('span', { class: 'muted small' }, '×'), count,
      btn('◀', 'Move towards the front', () => move(gi, gi - 1), gi === 0),
      btn('▶', 'Move towards the tail', () => move(gi, gi + 1), gi === t.groups.length - 1),
      btn('×', 'Remove from the train', () => { t.groups.splice(gi, 1); selected = -1; ctx!.changed(); render(); })),
    h('div', { class: 'te-group-row' }, role('leading', 'Leading cab', 'Its cab leads the train (ETCS, class B, train integrity)'),
      role('pantograph', 'Pantograph up', 'It runs with a pantograph raised (energy, current, pantographs)')),
    gi === t.groups.length - 1 ? h('div', { class: 'muted small' }, 'its last vehicle is the tail') : null);
}

function table(): HTMLElement {
  const t = ctx!.train;
  const c = ctx!.combined;
  let first = 1;
  const heads = t.groups.map((g, gi) => { const th = groupHead(g, gi, first); first += g.count; return th; });
  const rows: Node[] = [];
  let group: string | null | undefined;
  const cols = t.groups.length + 3;
  for (const r of groupedRules(ctx!.rules.rules)) {
    if (r.group !== group) {
      group = r.group;
      if (group) rows.push(h('tr', { class: 'te-sep' }, h('th', { colspan: cols, scope: 'colgroup' }, group)));
    }
    const src = c?.sources[r.key];
    const cells = t.groups.map((g, gi) => {
      const p = profileOf(g.profile);
      const scope = inScope(r.combine_over, t.groups, gi);
      if (r.combine_over === 'train') return h('td', { class: 'te-cell out' }, h('span', { class: 'muted small' }, OUT_OF_SCOPE.train));
      if (!p) return h('td', { class: 'te-cell out' }, '');
      const cur = p.values[r.key];
      const sets = !!src && !src.entered && src.from.includes(gi) && t.groups.length > 1;
      const missing = !!src && src.missing.includes(gi);
      return h('td', { class: 'te-cell' + (scope ? '' : ' out') + (sets ? ' sets' : '') + (missing ? ' missing' : ''), 'data-group': gi },
        cellEditor(r, cur, (v) => { ctx!.setVehicleValue(g.profile, r.key, v); render(); }, `${r.label}, ${vehicleName(g.profile)}`),
        !scope ? h('div', { class: 'muted small' }, `not counted: ${OUT_OF_SCOPE[r.combine_over ?? 'all']}`) : null,
        sets ? h('div', { class: 'te-sets small' }, '● sets it') : null,
        missing ? h('div', { class: 'error small' }, 'missing: the train value is unknown') : null);
    });
    const trainCell = r.combine_over === 'train'
      ? h('td', { class: 'te-cell te-train' }, cellEditor(r, t.values[r.key], (v) => { ctx!.setTrainValue(r.key, v); render(); }, `${r.label}, whole train`))
      : h('td', { class: 'te-cell te-train' }, c ? (formatValue(r, c.profile[r.key], ctx!.label) || h('span', { class: 'muted' }, src?.missing.length ? 'unknown' : '—')) : '…');
    rows.push(h('tr', { 'data-rule': r.key },
      h('th', { scope: 'row', class: 'te-rule' }, r.label, r.constraint === 'input' ? h('div', { class: 'muted small' }, 'used by other checks') : null),
      ...cells, trainCell,
      h('td', { class: 'te-how small muted' }, r.combine_text ?? '')));
  }
  return h('div', { class: 'te-table-wrap' }, h('table', { class: 'te-table' },
    h('thead', {}, h('tr', {}, h('th', { scope: 'col', class: 'te-rule' }, 'Characteristic'), ...heads,
      h('th', { scope: 'col', class: 'te-train' }, 'Train'), h('th', { scope: 'col', class: 'te-how' }, 'How the train value is found'))),
    h('tbody', {}, ...rows)));
}

function addBar(): HTMLElement {
  const t = ctx!.train;
  const sel = h('select', { class: 'te-select', 'aria-label': 'Vehicle profile to add' },
    ...ctx!.profiles.map((p) => h('option', { value: p.id }, p.name)),
    h('option', { value: '__new' }, 'New vehicle profile…'));
  return h('div', { class: 'compat-bar' }, h('span', { class: 'small' }, 'Add at the tail:'), sel,
    h('button', { class: 'btn btn-small', type: 'button', onclick: () => {
      const id = sel.value === '__new' ? ctx!.newVehicle() : sel.value;
      t.groups.push({ profile: id, count: 1, roles: [] });
      selected = t.groups.length - 1;
      ctx!.changed(); render();
    } }, '+ Add vehicle'));
}

function render() {
  if (!root || !ctx) return;
  const scroll = root.querySelector('.te-table-wrap')?.scrollLeft ?? 0;
  const body = root.querySelector<HTMLElement>('.train-editor-body')!;
  const y = body.scrollTop;
  body.replaceChildren(
    drawing(), addBar(),
    h('p', { class: 'muted small' }, 'A column’s values belong to its vehicle profile: changing one changes every train that uses that vehicle. ',
      'Leave a value empty and the characteristic is not checked for the train.'),
    table());
  body.scrollTop = y;
  const wrap = root.querySelector('.te-table-wrap');
  if (wrap) wrap.scrollLeft = scroll;
  root.querySelector('.train-editor-name')!.textContent = ctx.train.name;
}

/** Opens the editor over the map (or refreshes it with a new context). */
export function openEditor(c: EditorCtx) {
  ctx = c;
  if (!root?.isConnected) {
    root = h('section', { class: 'train-editor', role: 'dialog', 'aria-modal': 'false', 'aria-labelledby': 'train-editor-title' },
      h('div', { class: 'train-editor-head' },
        h('div', { class: 'train-editor-title' }, h('h2', { id: 'train-editor-title' }, 'Train editor'),
          h('span', { class: 'train-editor-name muted' })),
        h('button', { class: 'btn btn-small', type: 'button', onclick: () => ctx?.close() }, 'Back to the panel'),
        h('button', { class: 'btn btn-small btn-primary', type: 'button', onclick: () => ctx?.showOnMap() }, 'Check on the map')),
      h('div', { class: 'train-editor-body' }));
    root.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !(e.target as HTMLElement).closest('select, details')) { e.stopPropagation(); ctx?.close(); } });
    document.querySelector('.map-wrap')?.append(root);
    document.body.classList.add('train-editor-open');
    render();
    root.querySelector<HTMLElement>('.train-editor-head button')?.focus();
    return;
  }
  render();
}

/** New combined values (or another train): redraw. */
export function refreshEditor(c: Partial<EditorCtx>) {
  if (!ctx || !isOpen()) return;
  Object.assign(ctx, c);
  render();
}

export function closeEditor() {
  root?.remove();
  root = null;
  ctx = null;
  selected = -1;
  document.body.classList.remove('train-editor-open');
}
