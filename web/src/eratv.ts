// Load a vehicle type from ERATV, the European register of authorised types of vehicles,
// into the vehicle profiles: one profile per configuration. The worker fetches the type
// (or reads an XML export the user uploads) and converts it with the ERA ontology
// (api/app/eratv.py, docs/eratv.md).
import { eratvApi, type CompatRule, type EratvInfo, type EratvListItem, type EratvRequest, type EratvType } from './api';
import { h } from './state';
import * as V from './vehicles';

export interface EratvCtx {
  rules: CompatRule[] | null;
  /** Adds the profiles to the user's list and shows the first one. */
  add: (profiles: V.VehicleProfile[], what: string) => void;
  /** Draws the panel again (this section included). */
  rerender: () => void;
}

const TYPE_ID = /\d{2}-\d{3}-\d{4}-\d(?:-\d{3}){1,2}/;
const POLL_MS = 1500;
const WAIT_MS = 4 * 60_000;

let info: EratvInfo | null | undefined;     // undefined: not asked yet; null: the API has no ERATV
let known: EratvListItem[] = [];
let input = '';
let busy = false;
let msg: { text: string; error?: boolean; reason?: string | null } | null = null;
let loaded: { type: EratvType; configs: V.EratvConfiguration[]; picked: Set<number> } | null = null;
let ctxRef: EratvCtx | null = null;
let run = 0;                                // a newer lookup makes older ones stop

const rerender = () => ctxRef?.rerender();
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function init() {
  if (info !== undefined) return;
  info = null;
  eratvApi.info().then((i) => { info = i; return i.stored ? eratvApi.list() : []; })
    .then((l) => { known = l; rerender(); })
    .catch(() => { info = null; });
}

async function follow(req: EratvRequest, mine: number): Promise<string | null> {
  const until = Date.now() + WAIT_MS;
  while (req.status === 'queued' || req.status === 'running') {
    if (mine !== run) return null;
    if (Date.now() > until) throw Object.assign(new Error('ERATV is taking too long to answer; try again later'), { reason: 'timeout' });
    await sleep(POLL_MS);
    if (req.id == null) break;
    req = await eratvApi.request(req.id);
  }
  if (req.status === 'failed') throw Object.assign(new Error(req.message || 'the lookup failed'), { reason: req.reason });
  return req.typeId;
}

async function lookup(start: () => Promise<EratvRequest>, what: string) {
  const mine = ++run;
  busy = true; loaded = null;
  msg = { text: what };
  rerender();
  try {
    const typeId = await follow(await start(), mine);
    if (mine !== run || !typeId) return;
    const type = await eratvApi.type(typeId);
    if (mine !== run) return;
    show(type);
    eratvApi.list().then((l) => { known = l; }).catch(() => undefined);
  } catch (e) {
    if (mine !== run) return;
    msg = { text: (e as Error).message, error: true, reason: (e as { reason?: string }).reason ?? null };
  } finally {
    if (mine === run) { busy = false; rerender(); }
  }
}

function show(type: EratvType) {
  if (!ctxRef?.rules) throw new Error('the compatibility rules have not loaded yet');
  const configs = V.eratvProfiles(type, ctxRef.rules);
  loaded = { type, configs, picked: new Set(configs.map((_, i) => i)) };
  const filled = Math.max(0, ...configs.map((c) => Object.keys(c.profile.values).length));
  msg = { text: `${filled} of ${ctxRef.rules.length} characteristics filled in from ERATV.` };
}

function load(refresh = false) {
  const m = input.match(TYPE_ID);
  if (!m) { msg = { text: 'Enter a Type ID such as 11-256-0001-3-002-001, or the link to the type’s page in ERATV.', error: true }; rerender(); return; }
  input = m[0];
  lookup(() => eratvApi.fetch(m[0], refresh), refresh ? `Fetching ${m[0]} from ERATV again…` : `Looking up ${m[0]}…`);
}

function upload(file: File) {
  lookup(() => eratvApi.upload(file), `Reading ${file.name}…`);
}

function addPicked() {
  if (!loaded || !ctxRef) return;
  const list = loaded.configs.filter((_, i) => loaded!.picked.has(i)).map((c) => c.profile);
  if (!list.length) return;
  const name = loaded.type.summary.name || loaded.type.typeId;
  loaded = null; msg = null;
  // fresh ids, so the same type can be added twice
  ctxRef.add(list.map((p) => ({ ...p, id: V.newId(), values: structuredClone(p.values) })),
    `${list.length} profile${list.length === 1 ? '' : 's'} from ERATV (${name})`);
}

function authorisations(t: EratvType): HTMLElement | null {
  const list = t.summary.authorisations ?? [];
  if (!list.length) return null;
  return h('details', { class: 'eratv-auth' },
    h('summary', {}, `Authorisations (${list.length}), for information`),
    h('ul', { class: 'small' }, ...list.map((a) => h('li', {},
      h('strong', {}, a.title || 'Authorisation'),
      a.areaOfUse?.length ? ` · area of use ${a.areaOfUse.join(', ')}` : '',
      a.status ? ` · ${a.status}` : '',
      a.validUntil ? ` · valid until ${a.validUntil}` : '',
      a.restrictions?.length ? h('div', { class: 'muted' }, 'Coded restrictions: ',
        [...new Set(a.restrictions.map((r) => `${r.code}${r.value ? ` ${r.value}` : ''}`))].join('; ')) : null,
      a.nonCoded?.length ? h('div', { class: 'muted' }, 'Non-coded restrictions: ', a.nonCoded.join('; ')) : null))),
    h('p', { class: 'muted small' }, 'Not part of the check: RINF Atlas compares technical characteristics only. ',
      'The authorisation decides where a vehicle may run.'));
}

function result(): HTMLElement | null {
  if (!loaded) return null;
  const { type: t, configs, picked } = loaded;
  const s = t.summary;
  const facts = [s.category, s.subcategory, s.manufacturer, s.maxDesignSpeed ? `${s.maxDesignSpeed} km/h` : null].filter(Boolean).join(' · ');
  const many = configs.length > 1;
  return h('div', { class: 'eratv-result' },
    h('p', { class: 'eratv-title' }, h('strong', {}, s.name || t.typeId), ' ',
      h('a', { href: t.url, target: '_blank', rel: 'noopener' }, t.typeId)),
    facts ? h('p', { class: 'muted small' }, facts) : null,
    h('p', { class: 'muted small' }, `Fetched ${t.fetchedAt.slice(0, 10)}. `,
      t.coverage != null ? `${Math.round(t.coverage * 100)}% of ERATV’s values converted. ` : '',
      h('button', { class: 'linklike small', type: 'button', disabled: busy || !info?.lookup, title: 'Fetch this type from ERATV again',
        onclick: () => { input = t.typeId; load(true); } }, 'Fetch again')),
    many ? h('p', { class: 'small' }, `${configs.length} configurations: each becomes a profile.`) : null,
    many ? h('ul', { class: 'eratv-configs small' }, ...configs.map((c, i) => h('li', {},
      h('label', {}, h('input', { type: 'checkbox', checked: picked.has(i),
        onchange: (e: Event) => { if ((e.target as HTMLInputElement).checked) picked.add(i); else picked.delete(i); rerender(); } }),
        ` ${c.label} `, h('span', { class: 'muted' }, `(${Object.keys(c.profile.values).length})`))))) : null,
    authorisations(t),
    h('div', { class: 'compat-bar' },
      h('button', { class: 'btn btn-small btn-primary', type: 'button', disabled: !picked.size, onclick: addPicked },
        many ? `Add ${picked.size} profile${picked.size === 1 ? '' : 's'}` : 'Add as a profile'),
      h('button', { class: 'btn btn-small', type: 'button', onclick: () => { loaded = null; msg = null; rerender(); } }, 'Cancel')));
}

/** The "From ERATV" block of the vehicle check panel. */
export function eratvSection(ctx: EratvCtx): HTMLElement {
  ctxRef = ctx;
  init();
  const lookupOn = info?.lookup !== false;
  const file = h('input', { type: 'file', accept: '.xml,application/xml,text/xml', hidden: true });
  file.addEventListener('change', () => { const f = file.files?.[0]; if (f) upload(f); file.value = ''; });
  const field = h('input', {
    type: 'text', class: 'eratv-input', value: input, list: 'eratv-known', spellcheck: false,
    placeholder: lookupOn ? 'Type ID or ERATV link' : 'Type ID of a type loaded before',
    'aria-label': 'ERATV Type ID or the link to the type’s page',
    oninput: (e: Event) => { input = (e.target as HTMLInputElement).value; },
    onkeydown: (e: Event) => { if ((e as KeyboardEvent).key === 'Enter') { e.preventDefault(); load(); } },
  });
  const datalist = h('datalist', { id: 'eratv-known' },
    ...known.map((k) => h('option', { value: k.typeId }, [k.name, k.manufacturer].filter(Boolean).join(' · '))));
  const stuck = msg?.error && ['captcha', 'blocked', 'limit', 'disabled', 'timeout'].includes(msg.reason ?? '');
  const m = input.match(TYPE_ID);
  return h('div', { class: 'compat-io eratv' },
    h('h2', { class: 'section-title' }, 'From ERATV'),
    h('p', { class: 'muted small' }, 'Start from an authorised vehicle type in ',
      h('a', { href: 'https://eratv.era.europa.eu/', target: '_blank', rel: 'noopener' }, 'ERATV'),
      ', the European register of authorised types of vehicles: enter its Type ID',
      lookupOn ? '' : ' (looking types up in ERATV is turned off here: upload its XML export)',
      '. Each configuration becomes a profile you can check and edit.'),
    h('div', { class: 'compat-bar' }, field, datalist,
      h('button', { class: 'btn btn-small', type: 'button', disabled: busy || !ctx.rules, onclick: () => load() }, busy ? 'Loading…' : 'Load'),
      h('button', { class: `btn btn-small${stuck ? ' btn-primary' : ''}`, type: 'button', disabled: busy || !ctx.rules,
        title: 'Upload the XML export of a type, downloaded from its page in ERATV', onclick: () => file.click() }, 'Upload XML…'), file),
    msg ? h('p', { class: `small ${msg.error ? 'error' : 'muted'}${busy ? ' loading-text' : ''}`, role: 'status' }, msg.text) : null,
    stuck && m ? h('p', { class: 'small' }, h('a', { href: `https://eratv.era.europa.eu/Eratv/Home/View/${m[0]}`, target: '_blank', rel: 'noopener' },
      `Open ${m[0]} in ERATV`), ', choose the XML export, then upload the file here.') : null,
    result());
}
