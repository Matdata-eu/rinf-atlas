// Vehicle profiles and trains for the compatibility check: kept in this browser (localStorage),
// exported / imported as JSON or (vehicles) as Turtle using era:VehicleType (ERA ontology).
// A train lists vehicle profiles front to back; its vehicles' values stay in the profiles.
import type { CompatRule, Train, VehicleValues } from './api';

export interface VehicleProfile {
  id: string;
  name: string;
  note?: string;
  /** One of the shipped, illustrative examples (may be edited like any other). */
  example?: boolean;
  values: VehicleValues;
}

export type Role = 'leading' | 'pantograph';
/** Identical vehicles in a row: a vehicle profile, how many, and what they do in the train. */
export interface TrainGroup { profile: string; count: number; roles: Role[] }
export interface TrainProfile {
  id: string;
  name: string;
  note?: string;
  example?: boolean;
  /** Front to back (the first group runs at the front, the last one is the tail). */
  groups: TrainGroup[];
  /** Values entered for the whole train (e.g. its braking deceleration). */
  values: VehicleValues;
}
export type Mode = 'vehicle' | 'train';

interface Saved {
  version: 2; profiles: VehicleProfile[]; active: string | null; show: boolean;
  trains: TrainProfile[]; activeTrain: string | null;
  /** What the check is run for: the active vehicle profile or the active train. */
  mode: Mode;
  /** Ids of the examples already offered, so new examples appear once and deleted ones stay deleted. */
  seen?: string[];
}

const KEY = 'atlas-vehicle-profiles';
const ERA = 'http://data.europa.eu/949/';
const C = `${ERA}concepts/`;
export const JSON_FORMAT = 'rinf-atlas-vehicle-profiles';

/** Labels of the concepts the examples use (ERA SKOS), for when the index has no label for them. */
export const KNOWN_LABELS: Record<string, string> = {
  [`${C}nominal-track-gauges/30`]: '1435', [`${C}nominal-track-gauges/70`]: '1668',
  [`${C}energy-supply-systems/AC10`]: 'AC 25kV-50Hz', [`${C}energy-supply-systems/AC20`]: 'AC 15kV-16.7Hz',
  [`${C}energy-supply-systems/DC30`]: 'DC 3kV', [`${C}energy-supply-systems/DC40`]: 'DC 1.5kV',
  [`${C}contact-line-systems/40`]: 'Not electrified',
  [`${C}compliant-pantograph-heads/10`]: '1950 mm (Type 1) with non-insulated horns',
  [`${C}compliant-pantograph-heads/20`]: '1600 mm (EP)',
  [`${C}compliant-pantograph-heads/50`]: '1950 mm (Type 1) with insulated horns',
  [`${C}other-pantograph-heads/20`]: '1950 mm (PL)', [`${C}other-pantograph-heads/40`]: '1760 mm (BE)',
  [`${C}other-pantograph-heads/70`]: '1450 mm', [`${C}other-pantograph-heads/100`]: '1700 mm (ES)', [`${C}other-pantograph-heads/130`]: '1950 mm (Type 1)',
  [`${C}etcs-levels/20`]: 'ETCS level 1', [`${C}etcs-levels/30`]: 'ETCS level 2', [`${C}etcs-levels/40`]: 'ETCS level 3',
  [`${C}etcs-levels/10`]: 'No ETCS installed', [`${C}etcs-levels/50`]: 'ETCS level 0', [`${C}etcs-levels/60`]: 'NTC',
  [`${C}gaugings/10`]: 'GA', [`${C}gaugings/20`]: 'GB', [`${C}gaugings/30`]: 'GC', [`${C}gaugings/40`]: 'G1',
  [`${C}gaugings/60`]: 'G2', [`${C}gaugings/340`]: 'GI1', [`${C}gaugings/350`]: 'GI2', [`${C}gaugings/360`]: 'GI3',
  [`${C}load-capability-line-categories/90`]: 'D4', [`${C}load-capability-line-categories/100`]: 'D4xL',
  [`${C}load-capability-line-categories/110`]: 'E4', [`${C}load-capability-line-categories/120`]: 'E5',
  [`${C}train-detection/10`]: 'track circuit', [`${C}train-detection/20`]: 'wheel detector', [`${C}train-detection/30`]: 'loop',
};

const FIRST_EXAMPLES = ['example-emu-25kv', 'example-loco-3kv', 'example-iberian', 'example-15kv'];
const A = 'https://rinf-atlas.matdata.eu/ns#';

/** Illustrative starting points, not authorised vehicle types. */
export function exampleProfiles(): VehicleProfile[] {
  const g = (n: string) => `${C}gaugings/${n}`;
  return [
    {
      id: 'example-emu-25kv', example: true, name: 'Example: standard-gauge 25 kV AC EMU with ETCS level 2',
      note: 'Illustrative values for a 200 m electric multiple unit. Not an authorised vehicle type.',
      values: {
        wheelSetGauge: [`${C}nominal-track-gauges/30`],
        energySupplySystem: [`${C}energy-supply-systems/AC10`],
        pantographHead: [`${C}compliant-pantograph-heads/20`],
        etcsLevel: [`${C}etcs-levels/20`, `${C}etcs-levels/30`],
        gaugingProfile: [g('40'), g('60'), g('10'), g('20'), g('30')],
        trainLength: 200,
      },
    },
    {
      id: 'example-loco-3kv', example: true, name: 'Example: 3 kV DC freight locomotive',
      note: 'Illustrative values for a standard-gauge freight locomotive (22.5 t axle load). Not an authorised vehicle type.',
      values: {
        wheelSetGauge: [`${C}nominal-track-gauges/30`],
        energySupplySystem: [`${C}energy-supply-systems/DC30`],
        pantographHead: [`${C}compliant-pantograph-heads/20`, `${C}other-pantograph-heads/20`, `${C}other-pantograph-heads/40`],
        lineCategory: ['90', '100', '110', '120'].map((n) => `${C}load-capability-line-categories/${n}`),
        gaugingProfile: [g('40'), g('10'), g('20')],
        trainLength: 18.9, maxSpeed: 140, axleLoad: 21.5, maxTrainCurrent: 1800,
        magneticBrake: [`${A}BrakeNotFitted`], redTailLights: [true],
      },
    },
    {
      id: 'example-iberian', example: true, name: 'Example: Iberian-gauge (1668 mm) 3 kV DC train',
      note: 'Illustrative values for a broad-gauge train on the Spanish conventional network. Not an authorised vehicle type.',
      values: {
        wheelSetGauge: [`${C}nominal-track-gauges/70`],
        energySupplySystem: [`${C}energy-supply-systems/DC30`],
        pantographHead: [`${C}other-pantograph-heads/100`],
      },
    },
    {
      id: 'example-15kv', example: true, name: 'Example: 15 kV 16.7 Hz AC locomotive',
      note: 'Illustrative values for a locomotive for the German, Austrian, Swiss and Scandinavian networks. Not an authorised vehicle type.',
      values: {
        wheelSetGauge: [`${C}nominal-track-gauges/30`],
        energySupplySystem: [`${C}energy-supply-systems/AC20`],
        pantographHead: [`${C}compliant-pantograph-heads/10`, `${C}compliant-pantograph-heads/50`, `${C}other-pantograph-heads/130`],
      },
    },
    {
      id: 'example-multisystem', example: true, name: 'Example: multi-system locomotive (AC 25 kV, AC 15 kV, DC 3 kV, DC 1.5 kV)',
      note: 'Illustrative values for a four-system standard-gauge locomotive for cross-border freight and passenger trains. Not an authorised vehicle type.',
      values: {
        wheelSetGauge: [`${C}nominal-track-gauges/30`],
        energySupplySystem: ['AC10', 'AC20', 'DC30', 'DC40'].map((n) => `${C}energy-supply-systems/${n}`),
        pantographHead: [`${C}compliant-pantograph-heads/10`, `${C}compliant-pantograph-heads/20`, `${C}compliant-pantograph-heads/50`,
          `${C}other-pantograph-heads/20`, `${C}other-pantograph-heads/40`, `${C}other-pantograph-heads/130`],
        etcsLevel: [`${C}etcs-levels/20`, `${C}etcs-levels/30`],
        gaugingProfile: [g('40'), g('10'), g('20')],
        lineCategory: ['90', '100', '110', '120'].map((n) => `${C}load-capability-line-categories/${n}`),
      },
    },
    {
      id: 'example-bimode', example: true, name: 'Example: bi-mode regional train (AC 25 kV and diesel)',
      note: 'Illustrative values for a 110 m regional unit that runs under 25 kV AC and on its diesel engines where there is no contact line. Not an authorised vehicle type.',
      values: {
        wheelSetGauge: [`${C}nominal-track-gauges/30`],
        energySupplySystem: [`${C}energy-supply-systems/AC10`, `${C}contact-line-systems/40`],
        pantographHead: [`${C}compliant-pantograph-heads/20`],
        trainLength: 110,
      },
    },
    {
      id: 'example-diesel-freight', example: true, name: 'Example: diesel freight locomotive',
      note: 'Illustrative values for a standard-gauge diesel locomotive. The energy supply system is left empty, so electrification is not checked. Not an authorised vehicle type.',
      values: {
        wheelSetGauge: [`${C}nominal-track-gauges/30`],
        gaugingProfile: [g('40'), g('10'), g('20')],
        lineCategory: ['90', '100', '110', '120'].map((n) => `${C}load-capability-line-categories/${n}`),
      },
    },
    {
      id: 'example-emu-1500v', example: true, name: 'Example: 1.5 kV DC EMU for the Dutch network',
      note: 'Illustrative values for a 160 m double-deck electric multiple unit. Not an authorised vehicle type.',
      values: {
        wheelSetGauge: [`${C}nominal-track-gauges/30`],
        energySupplySystem: [`${C}energy-supply-systems/DC40`],
        pantographHead: [`${C}compliant-pantograph-heads/10`, `${C}other-pantograph-heads/130`],
        trainLength: 160,
      },
    },
    {
      id: 'example-container-wagon', example: true, name: 'Example: loaded container wagon',
      note: 'Illustrative values for a four-axle container wagon at full load, for composing a freight train. Not an authorised vehicle type.',
      values: {
        wheelSetGauge: [`${C}nominal-track-gauges/30`],
        gaugingProfile: [g('40'), g('10'), g('20'), g('30')],
        lineCategory: ['90', '100', '110', '120'].map((n) => `${C}load-capability-line-categories/${n}`),
        trainLength: 19.7, maxSpeed: 100, axleLoad: 22.5, minWheelDiameter: 840,
        quiet: [false], magneticBrake: [`${A}BrakeNotFitted`], redTailLights: [true],
      },
    },
  ];
}

/** Illustrative trains made of the example vehicles. */
export function exampleTrains(): TrainProfile[] {
  return [
    {
      id: 'example-train-freight', example: true, name: 'Example freight: 2 × 3 kV locomotive + 18 wagons',
      note: 'Two locomotives in multiple, both with a pantograph raised, and 18 loaded container wagons. Not an authorised train.',
      groups: [
        { profile: 'example-loco-3kv', count: 1, roles: ['leading', 'pantograph'] },
        { profile: 'example-loco-3kv', count: 1, roles: ['pantograph'] },
        { profile: 'example-container-wagon', count: 18, roles: [] },
      ],
      values: { brakingDeceleration: 0.5 },
    },
    {
      id: 'example-train-emu-double', example: true, name: 'Example: two 25 kV EMUs coupled',
      note: 'Two 200 m multiple units in multiple, each with its own pantograph raised. Not an authorised train.',
      groups: [{ profile: 'example-emu-25kv', count: 1, roles: ['leading', 'pantograph'] },
        { profile: 'example-emu-25kv', count: 1, roles: ['pantograph'] }],
      values: {},
    },
  ];
}

/** The train as the API takes it: each group with its profile's name and values. Groups whose
 * profile no longer exists are left out. */
export function trainRequest(t: TrainProfile, profiles: VehicleProfile[]): Train {
  const byId = new Map(profiles.map((p) => [p.id, p]));
  const vehicles = t.groups.filter((g) => byId.has(g.profile)).map((g) => {
    const p = byId.get(g.profile)!;
    return { name: p.name.replace(/^Example: /, ''), count: g.count, roles: g.roles, values: filledValues(p) };
  });
  return { name: t.name, vehicles, values: filledValues(t) };
}

// ---------- browser storage ----------
export function loadSaved(): Saved {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      // version 1 had no trains
      const s = JSON.parse(raw) as Omit<Saved, 'version'> & { version?: number };
      if (s && Array.isArray(s.profiles)) {
        const profiles = s.profiles.map(clean).filter((p): p is VehicleProfile => !!p);
        const trains = (Array.isArray(s.trains) ? s.trains : []).map(cleanTrain).filter((t): t is TrainProfile => !!t);
        // Storage from before `seen` was offered the first four examples.
        const seen = new Set(Array.isArray(s.seen) ? s.seen : FIRST_EXAMPLES);
        const ex = exampleProfiles();
        const exT = exampleTrains();
        profiles.push(...ex.filter((x) => !seen.has(x.id) && !profiles.some((p) => p.id === x.id)));
        trains.push(...exT.filter((x) => !seen.has(x.id) && !trains.some((t) => t.id === x.id)));
        const out: Saved = {
          version: 2, profiles, active: s.active ?? null, show: !!s.show, trains,
          activeTrain: s.activeTrain ?? trains[0]?.id ?? null, mode: s.mode === 'train' ? 'train' : 'vehicle',
          seen: [...ex, ...exT].map((x) => x.id),
        };
        if ([...ex, ...exT].some((x) => !seen.has(x.id))) save(out);  // so a new example deleted later stays deleted
        return out;
      }
    }
  } catch { /* private mode, blocked storage or bad JSON: start from the examples */ }
  const profiles = exampleProfiles();
  const trains = exampleTrains();
  return { version: 2, profiles, active: profiles[0].id, show: false, trains, activeTrain: trains[0].id, mode: 'vehicle',
    seen: [...profiles, ...trains].map((x) => x.id) };
}

export function save(s: Saved) {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* not persisted; keeps working for this visit */ }
}

export const newId = () => `v-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;

function cleanValues(raw: unknown): VehicleValues {
  const values: VehicleValues = {};
  for (const [k, v] of Object.entries((raw as Record<string, unknown>) ?? {})) {
    if (typeof v === 'number' && Number.isFinite(v)) values[k] = v;
    else if (Array.isArray(v)) {
      const xs = v.filter((x): x is string | boolean => typeof x === 'string' || typeof x === 'boolean');
      if (xs.length) values[k] = xs;
    }
  }
  return values;
}

/** Validates one profile from storage or an import; drops values of the wrong shape. */
function clean(p: unknown): VehicleProfile | null {
  if (!p || typeof p !== 'object') return null;
  const o = p as Record<string, unknown>;
  if (typeof o.name !== 'string') return null;
  return {
    id: typeof o.id === 'string' && o.id ? o.id : newId(), name: o.name.slice(0, 200),
    note: typeof o.note === 'string' ? o.note.slice(0, 2000) : undefined, example: o.example === true || undefined,
    values: cleanValues(o.values),
  };
}

const ROLES: Role[] = ['leading', 'pantograph'];

function cleanTrain(t: unknown): TrainProfile | null {
  if (!t || typeof t !== 'object') return null;
  const o = t as Record<string, unknown>;
  if (typeof o.name !== 'string' || !Array.isArray(o.groups)) return null;
  const groups = o.groups.flatMap((g): TrainGroup[] => {
    if (!g || typeof g !== 'object') return [];
    const x = g as Record<string, unknown>;
    if (typeof x.profile !== 'string') return [];
    const count = typeof x.count === 'number' && Number.isFinite(x.count) ? Math.min(500, Math.max(1, Math.round(x.count))) : 1;
    const roles = Array.isArray(x.roles) ? ROLES.filter((r) => (x.roles as unknown[]).includes(r)) : [];
    return [{ profile: x.profile, count, roles }];
  }).slice(0, 200);
  return {
    id: typeof o.id === 'string' && o.id ? o.id : newId(), name: o.name.slice(0, 200),
    note: typeof o.note === 'string' ? o.note.slice(0, 2000) : undefined, example: o.example === true || undefined,
    groups, values: cleanValues(o.values),
  };
}

export function isFilled(v: VehicleValues[string] | undefined): boolean {
  return typeof v === 'number' ? Number.isFinite(v) : Array.isArray(v) && v.length > 0;
}

export function filledValues(p: { values: VehicleValues } | undefined): VehicleValues {
  return Object.fromEntries(Object.entries(p?.values ?? {}).filter(([, v]) => isFilled(v)));
}

// ---------- JSON ----------
/** Profiles and trains as JSON; a train's vehicle profiles must be among `profiles`. */
export function toJson(profiles: VehicleProfile[], trains: TrainProfile[] = []): string {
  return JSON.stringify({
    format: JSON_FORMAT, version: trains.length ? 2 : 1, exported: new Date().toISOString(),
    profiles: profiles.map(({ id, name, note, values }) => ({ id, name, note, values: filledValues({ values }) })),
    ...(trains.length ? {
      trains: trains.map(({ id, name, note, groups, values }) => ({ id, name, note, groups, values: filledValues({ values }) })),
    } : {}),
  }, null, 2);
}

export interface Imported { profiles: VehicleProfile[]; trains: TrainProfile[] }

function fromJson(text: string): Imported {
  const d = JSON.parse(text) as unknown;
  const o = d && typeof d === 'object' && !Array.isArray(d) ? d as { profiles?: unknown; trains?: unknown } : null;
  const list = Array.isArray(d) ? d : o && Array.isArray(o.profiles) ? o.profiles : o && Array.isArray(o.trains) ? [] : [d];
  return {
    profiles: list.map(clean).filter((p): p is VehicleProfile => !!p),
    trains: (o && Array.isArray(o.trains) ? o.trains : []).map(cleanTrain).filter((t): t is TrainProfile => !!t),
  };
}

// ---------- Turtle (era:VehicleType) ----------
const RDF_TYPE = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type';
const RDFS = 'http://www.w3.org/2000/01/rdf-schema#';
const XSD = 'http://www.w3.org/2001/XMLSchema#';
const PREFIXES: [string, string][] = [['era', ERA], ['rdfs', RDFS], ['xsd', XSD]];

const ttlString = (s: string) => `"${s.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n').replace(/\r/g, '\\r')}"`;
const ttlIri = (iri: string) => {
  for (const [p, ns] of PREFIXES) {
    const local = iri.slice(ns.length);
    if (iri.startsWith(ns) && /^[A-Za-z_][A-Za-z0-9_-]*$/.test(local)) return `${p}:${local}`;
  }
  return `<${iri.replace(/[<>"{}|^`\\\s]/g, encodeURIComponent)}>`;
};

function ttlValue(v: string | boolean | number, rule: CompatRule): string {
  if (typeof v === 'boolean') return String(v);
  if (typeof v === 'number') {
    const dt = rule.datatype ?? `${XSD}decimal`;
    const x = rule.path_factor ? v * rule.path_factor : v;
    return `${ttlString(String(dt.endsWith('#integer') ? Math.round(x) : x))}^^${ttlIri(dt)}`;
  }
  return /^(https?:|urn:)/.test(v) ? ttlIri(v) : ttlString(v);
}

/** Profiles as era:VehicleType instances; characteristics follow the rules' ERA paths. */
export function toTurtle(profiles: VehicleProfile[], rules: CompatRule[]): string {
  const out = [
    '# Vehicle profiles exported from RINF Atlas. Hand-made profiles for a lightweight',
    '# compatibility check: illustrative, not authorised vehicle types (see ERATV).',
    `# Exported ${new Date().toISOString()}`,
    ...PREFIXES.map(([p, ns]) => `PREFIX ${p}: <${ns}>`), '',
  ];
  for (const p of profiles) {
    const direct: string[] = [`a era:VehicleType`, `rdfs:label ${ttlString(p.name)}`];
    if (p.note) direct.push(`rdfs:comment ${ttlString(p.note)}`);
    const nested = new Map<string, string[]>();
    for (const r of rules) {
      let v = p.values[r.key];
      if (!isFilled(v)) continue;
      if (r.options?.length) {
        // a choice stands for the boolean ERA records (e.g. era:hasMagneticBrake); atlas: paths keep the choice
        const bools = [...new Set(r.options.filter((o) => (v as unknown[]).includes(o.value) && typeof o.path_value === 'boolean')
          .map((o) => o.path_value as boolean))];
        if (bools.length) v = bools;
        else if (r.path[0]?.startsWith(ERA)) continue;
      }
      const objs = (Array.isArray(v) ? v : [v as number]).map((x) => ttlValue(x, r)).join(', ');
      if (r.path.length === 1) direct.push(`${ttlIri(r.path[0])} ${objs}`);
      else {
        // Two-step paths (era:configDependentParameter / ...) become a nested blank node.
        const head = ttlIri(r.path[0]);
        const rest = r.path.slice(1).map(ttlIri).join(' [ ');
        const close = ' ]'.repeat(r.path.length - 2);
        nested.set(head, [...(nested.get(head) ?? []), `${rest} ${objs}${close}`]);
      }
    }
    for (const [head, parts] of nested) direct.push(`${head} [\n        ${parts.join(' ;\n        ')}\n    ]`);
    out.push(`<urn:rinf-atlas:vehicle-type:${encodeURIComponent(p.id)}>\n    ${direct.join(' ;\n    ')} .`, '');
  }
  return out.join('\n');
}

type Term = { t: 'iri' | 'bnode'; v: string } | { t: 'lit'; v: string; dt?: string; lang?: string };
type TripleT = [Term, Term, Term];

/** A small Turtle parser: prefixes, IRIs, prefixed names, literals, numbers, booleans,
 * blank node property lists, `;` and `,`. Collections are not supported. */
export function parseTurtle(text: string): TripleT[] {
  const tokRe = /\s+|#[^\n]*|<([^<>"{}|^`\\\s]*)>|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|@prefix\b|@base\b|@([a-zA-Z]+(?:-[a-zA-Z0-9]+)*)|\^\^|_:([A-Za-z0-9_.-]*[A-Za-z0-9_-])|([+-]?(?:\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?))|((?:[A-Za-z][\w.-]*)?:(?:[\w%-](?:[\w.%-]*[\w%-])?)?)|([A-Za-z]+)|([.;,[\]()])/gy;
  const toks: { k: string; v: string }[] = [];
  let m: RegExpExecArray | null;
  let pos = 0;
  while (pos < text.length) {
    tokRe.lastIndex = pos;
    m = tokRe.exec(text);
    if (!m) throw new Error(`Turtle syntax error near “${text.slice(pos, pos + 30)}”`);
    pos = tokRe.lastIndex;
    if (m[1] !== undefined) toks.push({ k: 'iri', v: m[1] });
    else if (m[2] !== undefined) toks.push({ k: 'str', v: m[2] });
    else if (m[0] === '@prefix' || m[0] === '@base') toks.push({ k: 'kw', v: m[0].slice(1).toUpperCase() });
    else if (m[3] !== undefined) toks.push({ k: 'lang', v: m[3] });
    else if (m[0] === '^^') toks.push({ k: '^^', v: '' });
    else if (m[4] !== undefined) toks.push({ k: 'bnode', v: m[4] });
    else if (m[5] !== undefined) toks.push({ k: 'num', v: m[5] });
    else if (m[6] !== undefined) toks.push({ k: 'pname', v: m[6] });
    else if (m[7] !== undefined) toks.push({ k: 'word', v: m[7] });
    else if (m[8] !== undefined) toks.push({ k: m[8], v: m[8] });
  }
  const prefixes: Record<string, string> = {};
  const triples: TripleT[] = [];
  let i = 0;
  let bn = 0;
  const peek = () => toks[i];
  const next = () => { const t = toks[i++]; if (!t) throw new Error('unexpected end of Turtle'); return t; };
  const expect = (k: string) => { const t = next(); if (t.k !== k) throw new Error(`expected “${k}” in Turtle, got “${t.v}”`); };
  const unescape = (s: string) => s.replace(/\\(u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|.)/g, (_, e: string) =>
    e.length > 1 ? String.fromCodePoint(parseInt(e.slice(1), 16)) : ({ n: '\n', r: '\r', t: '\t', b: '\b', f: '\f' } as Record<string, string>)[e] ?? e);
  const pname = (v: string): string => {
    const k = v.indexOf(':');
    const ns = prefixes[v.slice(0, k)];
    if (ns === undefined) throw new Error(`unknown prefix “${v.slice(0, k)}:” in Turtle`);
    return ns + v.slice(k + 1).replace(/\\(.)/g, '$1');
  };
  const iriOf = (t: { k: string; v: string }): string => {
    if (t.k === 'iri') return t.v;
    if (t.k === 'pname') return pname(t.v);
    throw new Error(`expected an IRI in Turtle, got “${t.v}”`);
  };

  const object = (): Term => {
    const t = next();
    if (t.k === 'iri' || t.k === 'pname') return { t: 'iri', v: iriOf(t) };
    if (t.k === 'bnode') return { t: 'bnode', v: t.v };
    if (t.k === '[') return blankList();
    if (t.k === 'num') return { t: 'lit', v: t.v, dt: `${XSD}${/[eE]/.test(t.v) ? 'double' : t.v.includes('.') ? 'decimal' : 'integer'}` };
    if (t.k === 'word' && (t.v === 'true' || t.v === 'false')) return { t: 'lit', v: t.v, dt: `${XSD}boolean` };
    if (t.k === 'str') {
      const q = t.v.startsWith('"""') || t.v.startsWith("'''") ? 3 : 1;
      const v = unescape(t.v.slice(q, -q));
      if (peek()?.k === 'lang') return { t: 'lit', v, lang: next().v };
      if (peek()?.k === '^^') { next(); return { t: 'lit', v, dt: iriOf(next()) }; }
      return { t: 'lit', v };
    }
    if (t.k === '(') throw new Error('Turtle collections are not supported');
    throw new Error(`unexpected “${t.v}” in Turtle`);
  };
  const predicateObjects = (subj: Term) => {
    for (;;) {
      const p = next();
      const pred: Term = p.k === 'word' && p.v === 'a' ? { t: 'iri', v: RDF_TYPE } : { t: 'iri', v: iriOf(p) };
      for (;;) {
        triples.push([subj, pred, object()]);
        if (peek()?.k !== ',') break;
        next();
      }
      while (peek()?.k === ';') next();
      const n = peek();
      if (!n || n.k === '.' || n.k === ']') return;
    }
  };
  function blankList(): Term {
    const b: Term = { t: 'bnode', v: `_b${++bn}` };
    if (peek()?.k === ']') { next(); return b; }
    predicateObjects(b);
    expect(']');
    return b;
  }

  while (i < toks.length) {
    const t = next();
    if (t.k === 'kw' || (t.k === 'word' && /^(prefix|base)$/i.test(t.v))) {
      const kind = t.v.toUpperCase();
      if (kind === 'PREFIX') {
        const p = next();
        if (p.k !== 'pname' || !p.v.endsWith(':')) throw new Error('bad PREFIX in Turtle');
        prefixes[p.v.slice(0, -1)] = iriOf(next());
      } else next();
      if (t.k === 'kw') expect('.');
      continue;
    }
    let subj: Term;
    if (t.k === '[') {
      subj = blankList();
      if (peek()?.k === '.') { next(); continue; }
    } else if (t.k === 'bnode') subj = { t: 'bnode', v: t.v };
    else subj = { t: 'iri', v: iriOf(t) };
    predicateObjects(subj);
    expect('.');
  }
  return triples;
}

function fromTurtle(text: string, rules: CompatRule[]): VehicleProfile[] {
  const triples = parseTurtle(text);
  const key = (t: Term) => `${t.t}:${t.v}`;
  const out = new Map<string, TripleT[]>();
  for (const tr of triples) out.set(key(tr[0]), [...(out.get(key(tr[0])) ?? []), tr]);
  const objs = (s: Term, p: string) => (out.get(key(s)) ?? []).filter((tr) => tr[1].v === p).map((tr) => tr[2]);
  const vehicles = triples.filter((tr) => tr[1].v === RDF_TYPE && tr[2].v === `${ERA}VehicleType`).map((tr) => tr[0]);
  if (!vehicles.length) throw new Error('no era:VehicleType found in the Turtle file');
  return vehicles.map((s) => {
    const values: VehicleValues = {};
    for (const r of rules) {
      let nodes: Term[] = [s];
      for (const p of r.path) nodes = nodes.flatMap((n) => objs(n, p));
      if (!nodes.length) continue;
      if (r.kind === 'number') {
        const n = Number(nodes[0].v) / (r.path_factor || 1);
        if (Number.isFinite(n)) values[r.key] = n;
      } else if (r.kind === 'boolean') values[r.key] = [...new Set(nodes.map((n) => n.v === 'true' || n.v === '1'))];
      else if (r.options?.length && nodes.every((n) => n.t === 'lit')) {
        // ERA's boolean: the most restrictive choice with that value (e.g. true: used as a service brake)
        const want = nodes.some((n) => n.v === 'true' || n.v === '1');
        const pick = r.options.filter((o) => o.path_value === want).sort((a, b) => b.order - a.order)[0];
        if (pick) values[r.key] = [pick.value];
      } else values[r.key] = [...new Set(nodes.map((n) => n.v))];
    }
    const lit = (p: string) => objs(s, p).find((o) => o.t === 'lit')?.v;
    const urn = s.t === 'iri' && s.v.startsWith('urn:rinf-atlas:vehicle-type:') ? decodeURIComponent(s.v.slice(28)) : undefined;
    return { id: urn ?? newId(), name: lit(`${RDFS}label`) ?? (s.t === 'iri' ? s.v : 'Imported vehicle type'), note: lit(`${RDFS}comment`), values };
  });
}

/** Parses an export (JSON from this app, or Turtle with era:VehicleType instances). */
export function importProfiles(text: string, rules: CompatRule[]): Imported {
  const t = text.trim();
  const out = t.startsWith('{') || t.startsWith('[') ? fromJson(t) : { profiles: fromTurtle(t, rules), trains: [] };
  if (!out.profiles.length && !out.trains.length) throw new Error('no vehicle profiles found');
  return out;
}
