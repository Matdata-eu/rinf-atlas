// RINF Atlas mock API (plain Node http, no dependencies).
// Implements every endpoint of docs/api.md with deterministic fake data
// for Belgium and the Netherlands. Listens on PORT (default 8000).
import http from 'node:http';
import { URL } from 'node:url';

const PORT = Number(process.env.PORT || 8000);
const ERA = 'http://data.europa.eu/949/';
const FI = `${ERA}functionalInfrastructure/`;
const COUNTRY = 'http://publications.europa.eu/resource/authority/country/';

// ---------- deterministic RNG ----------
let seed = 42;
const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
const pick = (arr) => arr[Math.floor(rnd() * arr.length)];
const between = (a, b) => a + rnd() * (b - a);

// ---------- reference data ----------
const TOWNS = [
  ['Brussel-Zuid', 4.336, 50.836, 'BEL'], ['Brussel-Centraal', 4.357, 50.845, 'BEL'], ['Brussel-Noord', 4.361, 50.860, 'BEL'],
  ['Antwerpen-Centraal', 4.421, 51.217, 'BEL'], ['Gent-Sint-Pieters', 3.710, 51.036, 'BEL'], ['Brugge', 3.217, 51.197, 'BEL'],
  ['Oostende', 2.925, 51.228, 'BEL'], ['Leuven', 4.716, 50.881, 'BEL'], ['Mechelen', 4.483, 51.018, 'BEL'],
  ['Liège-Guillemins', 5.567, 50.624, 'BEL'], ['Namur', 4.862, 50.468, 'BEL'], ['Charleroi-Central', 4.438, 50.405, 'BEL'],
  ['Mons', 3.942, 50.453, 'BEL'], ['Tournai', 3.397, 50.613, 'BEL'], ['Kortrijk', 3.264, 50.825, 'BEL'],
  ['Hasselt', 5.327, 50.931, 'BEL'], ['Genk', 5.495, 50.966, 'BEL'], ['Arlon', 5.810, 49.680, 'BEL'],
  ['Aalst', 4.039, 50.942, 'BEL'], ['Sint-Niklaas', 4.143, 51.171, 'BEL'], ['Lier', 4.565, 51.135, 'BEL'],
  ['Ottignies', 4.570, 50.673, 'BEL'], ['Verviers-Central', 5.856, 50.589, 'BEL'], ['Dendermonde', 4.101, 51.022, 'BEL'],
  ['Roeselare', 3.124, 50.947, 'BEL'], ['Turnhout', 4.936, 51.322, 'BEL'], ['Herentals', 4.828, 51.181, 'BEL'],
  ['Landen', 5.082, 50.749, 'BEL'], ['Ath', 3.777, 50.627, 'BEL'], ['Libramont', 5.379, 49.920, 'BEL'],
  ['Amsterdam Centraal', 4.900, 52.379, 'NLD'], ['Rotterdam Centraal', 4.469, 51.925, 'NLD'], ['Den Haag Centraal', 4.324, 52.081, 'NLD'],
  ['Utrecht Centraal', 5.110, 52.089, 'NLD'], ['Eindhoven Centraal', 5.479, 51.443, 'NLD'], ['Breda', 4.780, 51.595, 'NLD'],
  ['Roosendaal', 4.458, 51.540, 'NLD'], ['Tilburg', 5.084, 51.560, 'NLD'], ['\'s-Hertogenbosch', 5.294, 51.690, 'NLD'],
  ['Nijmegen', 5.853, 51.843, 'NLD'], ['Arnhem Centraal', 5.901, 51.985, 'NLD'], ['Zwolle', 6.091, 52.505, 'NLD'],
  ['Groningen', 6.564, 53.211, 'NLD'], ['Leeuwarden', 5.793, 53.196, 'NLD'], ['Amersfoort Centraal', 5.375, 52.153, 'NLD'],
  ['Venlo', 6.172, 51.364, 'NLD'], ['Maastricht', 5.705, 50.849, 'NLD'], ['Heerlen', 5.974, 50.891, 'NLD'],
  ['Haarlem', 4.637, 52.388, 'NLD'], ['Leiden Centraal', 4.482, 52.166, 'NLD'], ['Dordrecht', 4.668, 51.808, 'NLD'],
  ['Vlissingen', 3.597, 51.444, 'NLD'], ['Middelburg', 3.616, 51.495, 'NLD'], ['Enschede', 6.897, 52.222, 'NLD'],
  ['Deventer', 6.161, 52.257, 'NLD'], ['Hengelo', 6.794, 52.262, 'NLD'], ['Alkmaar', 4.739, 52.638, 'NLD'],
  ['Den Helder', 4.761, 52.955, 'NLD'], ['Gouda', 4.703, 52.017, 'NLD'], ['Venray', 5.977, 51.522, 'NLD'],
];
const SUFFIX = {
  BEL: ['Noord', 'Zuid', 'Oost', 'West', 'Vorming', 'Bundel', 'Aansluiting', 'Kaai', 'Dorp', 'Haven'],
  NLD: ['Noord', 'Zuid', 'Oost', 'West', 'Emplacement', 'Aansluiting', 'Goederen', 'Haven', 'Stadion', 'Centrum'],
};
const OP_TYPES = [
  ['10', 'station'], ['20', 'small station'], ['30', 'passenger stop'], ['40', 'freight terminal'],
  ['50', 'depot or workshop'], ['60', 'train technical services'], ['70', 'passenger terminal'], ['80', 'junction'],
  ['90', 'border point'], ['100', 'shunting yard'], ['110', 'technical change'], ['120', 'switch'],
];
const IMS = { BEL: [['0076', 'Infrabel']], NLD: [['0084', 'ProRail'], ['0085', 'Keyrail']] };
const SIGNAL_TYPES = [['main', 'Main signal'], ['distant', 'Distant signal'], ['shunting', 'Shunting signal'], ['repeater', 'Repeater signal']];
const SWITCH_TYPES = [['simple', 'Simple switch'], ['double-slip', 'Double slip switch'], ['crossing', 'Diamond crossing'], ['three-way', 'Three-way switch']];

const CLASSES = [
  { class: 'operational-point', class_iri: `${ERA}OperationalPoint`, label: 'Operational point', color: '#e6194b', geometry: 'Point', min_zoom: 0 },
  { class: 'section-of-line', class_iri: `${ERA}SectionOfLine`, label: 'Section of line', color: '#4363d8', geometry: 'LineString' },
  { class: 'tunnel', class_iri: `${ERA}Tunnel`, label: 'Tunnel', color: '#9a6324', geometry: 'LineString' },
  { class: 'switch', class_iri: `${ERA}Switch`, label: 'Switch', color: '#3cb44b', geometry: 'Point', min_zoom: 11 },
  { class: 'signal', class_iri: `${ERA}Signal`, label: 'Signal', color: '#f58231', geometry: 'Point', min_zoom: 11 },
  // Defined by the shapes but nothing indexed: the UI hides it.
  { class: 'etcs-level', class_iri: `${ERA}ETCSLevel`, label: 'ETCS level', color: '#808000', geometry: 'Point' },
  { class: 'platform', class_iri: `${ERA}PlatformEdge`, label: 'Platform edge', color: '#911eb4', geometry: 'Polygon' },
];
const FACETS = [
  { key: 'country', class: '*', label: 'Country', kind: 'terms', datatype: 'iri', position: 1 },
  { key: 'im', class: '*', label: 'Infrastructure manager', kind: 'terms', datatype: 'iri', position: 2 },
  { key: 'op-type', class: 'operational-point', label: 'Operational point type', kind: 'terms', datatype: 'iri', position: 10 },
  { key: 'uopid', class: 'operational-point', label: 'Primary location code (UOPID)', kind: 'terms', datatype: 'string', position: 11 },
  { key: 'length', class: 'section-of-line', label: 'Length', kind: 'range', datatype: 'xsd:double', position: 20, unit: 'M' },
  { key: 'max-speed', class: 'section-of-line', label: 'Maximum speed', kind: 'range', datatype: 'xsd:integer', position: 21, unit: 'KiloM-PER-HR' },
  { key: 'electrified', class: 'section-of-line', label: 'Electrified', kind: 'boolean', datatype: 'xsd:boolean', position: 22 },
  { key: 'tunnel-length', class: 'tunnel', label: 'Tunnel length', kind: 'range', datatype: 'xsd:double', position: 30, unit: 'M' },
  { key: 'emergency-plan', class: 'tunnel', label: 'Emergency plan', kind: 'boolean', datatype: 'xsd:boolean', position: 31 },
  { key: 'switch-type', class: 'switch', label: 'Switch type', kind: 'terms', datatype: 'iri', position: 40 },
  { key: 'signal-type', class: 'signal', label: 'Signal type', kind: 'terms', datatype: 'iri', position: 50 },
  { key: 'platform-height', class: 'platform', label: 'Platform height', kind: 'range', datatype: 'xsd:integer', position: 60, unit: 'MilliM' },
  // Written by the worker's validation against ERA's SHACL shapes (worst severity per feature).
  { key: 'quality', class: '*', label: 'Data quality (ERA SHACL)', kind: 'terms', datatype: 'iri', position: 900 },
];
const FACET_BY_KEY = Object.fromEntries(FACETS.map((f) => [f.key, f]));

// ---------- generate features ----------
const features = []; // {id, iri, class, label, geometry, facets:{key:[{value,label}]}, extra}
const labels = {}; // iri -> label for concepts
const term = (value, label) => { labels[value] = label; return { value, label }; };
const countryTerm = (c) => term(COUNTRY + c, c === 'BEL' ? 'Belgium' : 'Netherlands');
const imTerm = (c) => { const [code, name] = pick(IMS[c]); return term(`${ERA}organisations/${code}`, name); };

const ops = [];
let opn = 0;
const usedLabels = new Set();
for (const [name, lon, lat, c] of TOWNS) {
  const n = 3 + Math.floor(rnd() * 4);
  for (let i = 0; i < n; i++) {
    const main = i === 0;
    const stem = name.split(/[- ]/)[0].length > 3 ? name.split(/[- ]/)[0] : name.replace(/ Centraal$/, '');
    let label = main ? name : `${stem} ${pick(SUFFIX[c])}`;
    for (let k = 2; usedLabels.has(label); k++) label = `${label.replace(/ \d+$/, '')} ${k}`;
    usedLabels.add(label);
    const [code, tlabel] = main ? (rnd() < 0.7 ? OP_TYPES[0] : OP_TYPES[6]) : pick(OP_TYPES);
    const id = `${c === 'BEL' ? 'BE' : 'NL'}${String(++opn).padStart(5, '0')}`;
    const op = {
      id, iri: `${FI}operationalPoints/${id}`, class: 'operational-point', label,
      geometry: { type: 'Point', coordinates: main ? [lon, lat] : [lon + between(-0.12, 0.12), lat + between(-0.08, 0.08)] },
      country: c,
      facets: {
        country: [countryTerm(c)], im: [imTerm(c)],
        'op-type': [term(`${ERA}concepts/op-types/rinf/${code}`, tlabel)],
        uopid: [{ value: id, label: id }],
      },
    };
    ops.push(op); features.push(op);
  }
}

const dist = (a, b) => {
  const [x1, y1] = a.geometry.coordinates, [x2, y2] = b.geometry.coordinates;
  const dx = (x2 - x1) * 111320 * Math.cos(((y1 + y2) / 2) * Math.PI / 180), dy = (y2 - y1) * 110540;
  return Math.sqrt(dx * dx + dy * dy);
};
const seen = new Set();
const sols = [];
for (const a of ops) {
  const near = ops.filter((b) => b !== a).sort((p, q) => dist(a, p) - dist(a, q)).slice(0, 2);
  for (const b of near) {
    const key = [a.id, b.id].sort().join('_');
    if (seen.has(key)) continue;
    seen.add(key);
    const [x1, y1] = a.geometry.coordinates, [x2, y2] = b.geometry.coordinates;
    const mid = [(x1 + x2) / 2 + between(-0.01, 0.01), (y1 + y2) / 2 + between(-0.01, 0.01)];
    const length = Math.round(dist(a, b) * 1.08);
    const sol = {
      id: `SOL_${key}`, iri: `${FI}sectionsOfLine/${key}`, class: 'section-of-line', label: `${a.label} – ${b.label}`,
      geometry: { type: 'LineString', coordinates: [[x1, y1], mid, [x2, y2]] }, country: a.country,
      opStart: a, opEnd: b,
      facets: {
        country: [countryTerm(a.country)], im: [imTerm(a.country)],
        length: [{ value: length, label: String(length) }],
        'max-speed': [(() => { const v = pick([40, 60, 80, 100, 120, 140, 160, 200, 300]); return { value: v, label: String(v) }; })()],
        electrified: [(() => { const v = rnd() < 0.8; return { value: v, label: v ? 'yes' : 'no' }; })()],
      },
    };
    sols.push(sol); features.push(sol);
  }
}

let tn = 0;
for (const sol of sols.filter(() => rnd() < 0.1)) {
  const [p, q] = [sol.geometry.coordinates[0], sol.geometry.coordinates[1]];
  const f0 = between(0.2, 0.6), f1 = f0 + between(0.1, 0.35);
  const lerp = (t) => [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t];
  const len = Math.round(sol.facets.length[0].value * (f1 - f0) * 2);
  const id = `T${String(++tn).padStart(4, '0')}`;
  const emergency = rnd() < 0.6;
  features.push({
    id, iri: `${FI}tunnels/${id}`, class: 'tunnel', label: `Tunnel ${sol.opStart.label.split(' ')[0]} ${tn}`,
    geometry: { type: 'LineString', coordinates: [lerp(f0), lerp(f1)] }, country: sol.country, sol,
    facets: {
      country: sol.facets.country, im: sol.facets.im,
      'tunnel-length': [{ value: len, label: String(len) }],
      'emergency-plan': [{ value: emergency, label: emergency ? 'yes' : 'no' }],
    },
  });
}

let swn = 0, sgn = 0, pfn = 0;
for (const op of ops) {
  const [lon, lat] = op.geometry.coordinates;
  const common = { country: op.facets.country, im: op.facets.im };
  for (let i = 0, n = Math.floor(rnd() * 2.2); i < n; i++) {
    const [k, l] = pick(SWITCH_TYPES);
    const id = `SW${String(++swn).padStart(5, '0')}`;
    features.push({ id, iri: `${FI}switches/${id}`, class: 'switch', label: `Switch ${op.label} ${i + 1}`, op, country: op.country,
      geometry: { type: 'Point', coordinates: [lon + between(-0.004, 0.004), lat + between(-0.003, 0.003)] },
      facets: { ...common, 'switch-type': [term(`${ERA}concepts/switch-types/${k}`, l)] } });
  }
  for (let i = 0, n = Math.floor(rnd() * 2.6); i < n; i++) {
    const [k, l] = pick(SIGNAL_TYPES);
    const id = `SG${String(++sgn).padStart(5, '0')}`;
    features.push({ id, iri: `${FI}signals/${id}`, class: 'signal', label: `Signal ${op.id}-${String.fromCharCode(65 + i)}`, op, country: op.country,
      geometry: { type: 'Point', coordinates: [lon + between(-0.006, 0.006), lat + between(-0.004, 0.004)] },
      facets: { ...common, 'signal-type': [term(`${ERA}concepts/signal-types/${k}`, l)] } });
  }
  if (rnd() < 0.25) {
    const id = `PE${String(++pfn).padStart(5, '0')}`;
    const h = pick([280, 550, 760, 840]);
    const w = 0.0025, d = 0.0003;
    features.push({ id, iri: `${FI}platformEdges/${id}`, class: 'platform', label: `Platform ${op.label} 1`, op, country: op.country,
      geometry: { type: 'Polygon', coordinates: [[[lon - w, lat - d], [lon + w, lat - d], [lon + w, lat + d], [lon - w, lat + d], [lon - w, lat - d]]] },
      facets: { ...common, 'platform-height': [{ value: h, label: String(h) }] } });
  }
}
for (const f of features) f.graph = `${ERA}graph/rinf/${f.country}`;
const byIri = new Map(features.map((f) => [f.iri, f]));

// ---------- data quality (SHACL validation results, like the worker stores them) ----------
const SH = 'http://www.w3.org/ns/shacl#';
const SHAPES = `${ERA}shapes/`;
const NO_ISSUES = 'https://rinf-atlas.matdata.eu/ns#NoIssues';
const SEV = { 3: 'violation', 2: 'warning', 1: 'info' };
const SEV_TERM = { 3: term(`${SH}Violation`, 'Violation'), 2: term(`${SH}Warning`, 'Warning'), 1: term(`${SH}Info`, 'Info') };
term(NO_ISSUES, 'No issues');
const RULES = [
  { sev: 3, shape: 'OperationalPointShape', comp: 'MinCountConstraintComponent', path: `${ERA}uopid`, message: 'Each operational point must have exactly one primary location code (uopid).' },
  { sev: 3, shape: 'SectionOfLineShape', comp: 'DatatypeConstraintComponent', path: `${ERA}lengthOfSectionOfLine`, message: 'The length of a section of line must be an xsd:double.', value: '"12,5"' },
  { sev: 2, shape: 'SectionOfLineShapeWarning', comp: 'MinCountConstraintComponent', path: `${ERA}solNature`, message: 'The nature of the section of line should be given.' },
  { sev: 2, shape: 'SignalShapeSPARQLWarning', comp: 'SPARQLConstraintComponent', path: null, message: 'The signal is not located on a track of the operational point it belongs to.' },
  { sev: 2, shape: 'SwitchShape', comp: 'ClassConstraintComponent', path: `${ERA}switchType`, message: 'The value of era:switchType must be a skos:Concept of the switch types list.', value: `<${ERA}concepts/switch-types/unknown>` },
  { sev: 1, shape: 'TunnelShapeInfo', comp: 'MaxCountConstraintComponent', path: `${ERA}hasEmergencyPlan`, message: 'More than one emergency plan statement.' },
];
const ruleOf = (r) => RULES.filter((x) => x.sev === r);
for (const f of features) {
  const x = rnd();
  const worst = x < 0.12 ? 3 : x < 0.3 ? 2 : x < 0.36 ? 1 : 0;
  f.qaResults = [];
  for (let s = worst; s >= 1; s--) if (s === worst || rnd() < 0.3) {
    const r = pick(ruleOf(s));
    f.qaResults.push({ severity: SEV[s], message: r.message, path: r.path ? `<${r.path}>` : null, value: r.value ?? null,
      value_label: null, shape: SHAPES + r.shape, component: SH + r.comp, sev: s });
  }
  f.facets.quality = worst ? [...new Set(f.qaResults.map((r) => r.sev))].map((s) => SEV_TERM[s]) : [{ value: NO_ISSUES, label: 'No issues' }];
}
const qaLevel = (f) => (f.qaResults.length ? Math.max(...f.qaResults.map((r) => r.sev)) : 0);
// The shapes each environment validates with: PROD pins a tag of ERA's ontology repository,
// UAT uses ERA's mirrored shapes graph.
const SHAPES_SRC = {
  prod: { kind: 'tag', graph: 'urn:atlas:shapes:v3.3.4', label: 'era-ontology v3.3.4 (era-shacl/ERA-RINF-shapes.ttl)',
    url: 'https://gitlab.com/era-europa-eu/public/interoperable-data-programme/era-ontology/era-ontology/-/raw/v3.3.4/era-shacl/ERA-RINF-shapes.ttl',
    tag: 'v3.3.4', repo: 'era-europa-eu/public/interoperable-data-programme/era-ontology/era-ontology', file: 'era-shacl/ERA-RINF-shapes.ttl',
    version: '3.3.4', modified: '2026-05-18' },
  uat: { kind: 'graph', graph: `${ERA}graph/rinf/shacl`, label: "ERA's shapes graph (version 4.0.0)", url: `${ERA}graph/rinf/shacl`, version: '4.0.0' },
};
const MAX_TRIPLES = 15000000;
// Validated graphs (the DEU one failed to load in UAT; LUX is above the size limit, as an example).
const qaStatus = (graph) => (graph.endsWith('/LUX') ? 'skipped' : graph.endsWith('/DEU') ? null : 'ok');
function qaCounts(graph) {
  const fs = features.filter((f) => f.graph === graph);
  const n = (s) => fs.reduce((a, f) => a + f.qaResults.filter((r) => r.sev === s).length, 0);
  return { violations: n(3), warnings: n(2), infos: n(1), focus: fs.filter((f) => f.qaResults.length).length };
}
function reportTurtle(graph) {
  const fs = features.filter((f) => f.graph === graph && f.qaResults.length);
  const out = ['@prefix sh: <http://www.w3.org/ns/shacl#> .', '@prefix dct: <http://purl.org/dc/terms/> .', '',
    '@prefix atlas: <https://rinf-atlas.matdata.eu/ns#> .', '',
    `<urn:atlas:report:mock> a sh:ValidationReport ; sh:conforms ${fs.length ? 'false' : 'true'} ; dct:created "2026-10-13T03:40:00Z" ;`
    + ` atlas:shapesSource ${JSON.stringify(SHAPES_SRC.prod.label)} ; atlas:shapesTag "v3.3.4" ; atlas:shapesVersion "3.3.4" ; dct:source <${SHAPES_SRC.prod.url}> .`];
  for (const f of fs) for (const r of f.qaResults) {
    out.push(`<urn:atlas:report:mock> sh:result [ a sh:ValidationResult ; sh:focusNode <${f.iri}> ; sh:resultSeverity sh:${r.severity[0].toUpperCase()}${r.severity.slice(1)} ;`
      + ` sh:sourceShape <${r.shape}> ; sh:sourceConstraintComponent <${r.component}> ;${r.path ? ` sh:resultPath ${r.path} ;` : ''}${r.value ? ` sh:value ${r.value} ;` : ''}`
      + ` sh:resultMessage ${JSON.stringify(r.message)} ] .`);
  }
  return out.join('\n') + '\n';
}

// ---------- query evaluation ----------
const norm = (s) => String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const coordsOf = (g) => (g.type === 'Point' ? [g.coordinates] : g.type === 'LineString' ? g.coordinates : g.coordinates.flat());
const repPoint = (g) => { const c = coordsOf(g); return c[Math.floor(c.length / 2)]; };

function matchFilter(f, key, cond) {
  const vals = (f.facets[key] || []).map((v) => v.value);
  if (Array.isArray(cond)) return cond.length === 0 || vals.some((v) => cond.some((c) => String(c) === String(v)));
  if (cond && typeof cond === 'object') {
    return vals.some((v) => (cond.min == null || v >= cond.min) && (cond.max == null || v <= cond.max));
  }
  return true;
}
function matches(f, st, { skipClasses = false, skipFacet = null } = {}) {
  if (!skipClasses && st.classes?.length && !st.classes.includes(f.class)) return false;
  if (st.graphs?.length && !st.graphs.includes(f.graph)) return false;
  for (const [k, cond] of Object.entries(st.filters || {})) {
    if (k === skipFacet) continue;
    // "class/key" filters only constrain that class (like the real API).
    const [scope, fk] = k.includes('/') ? k.split('/', 2) : [null, k];
    if (scope ? f.class === scope && !matchFilter(f, fk, cond) : (f.facets[fk] !== undefined || FACET_BY_KEY[fk]?.class === f.class) && !matchFilter(f, fk, cond)) return false;
  }
  if (st.q && !norm(f.label).includes(norm(st.q)) && !norm(f.id).includes(norm(st.q))) return false;
  if (st.bbox) {
    const [w, s, e, n] = st.bbox;
    if (!coordsOf(f.geometry).some(([x, y]) => x >= w && x <= e && y >= s && y <= n)) return false;
  }
  return true;
}

function facets(st) {
  const base = features.filter((f) => matches(f, st));
  const by_class = {};
  for (const c of CLASSES) by_class[c.class] = 0;
  for (const f of features) if (matches(f, st, { skipClasses: true })) by_class[f.class]++;
  const out = {};
  const wanted = st.facets ?? FACETS.filter((f) => f.class === '*' || st.classes?.includes(f.class)).map((f) => f.key);
  for (const want of wanted) {
    const [scope, fkey] = want.includes('/') ? want.split('/', 2) : [null, want];
    const fc = FACET_BY_KEY[fkey];
    if (!fc) continue;
    // multi-select faceting: a facet's own filter is ignored when counting its values
    const set = (st.filters?.[want] != null ? features.filter((f) => matches(f, st, { skipFacet: want })) : base)
      .filter((f) => !scope || f.class === scope);
    if (fc.kind === 'range') {
      const vs = set.flatMap((f) => (f.facets[fc.key] || []).map((v) => v.value));
      out[want] = { kind: 'range', min: vs.length ? Math.min(...vs) : null, max: vs.length ? Math.max(...vs) : null };
      continue;
    }
    const counts = new Map();
    for (const f of set) for (const v of f.facets[fc.key] || []) {
      const k = String(v.value);
      const cur = counts.get(k) || { value: v.value, label: v.label, count: 0 };
      cur.count++; counts.set(k, cur);
    }
    const values = [...counts.values()].sort((a, b) => b.count - a.count || String(a.label).localeCompare(String(b.label)));
    // Like the real API: Yes/No labels on booleans, `more` when the term list was cut.
    if (fc.kind === 'boolean') for (const v of values) v.label = v.value ? 'Yes' : 'No';
    out[want] = fc.kind === 'boolean' ? { kind: 'boolean', values, more: false }
      : { kind: 'terms', values: values.slice(0, 50), more: values.length > 50 };
  }
  return { total: base.length, by_class, facets: out };
}

// ---------- describe ----------
const uri = (value) => ({ type: 'uri', value });
const lit = (value, datatype, lang) => ({ type: 'literal', value: String(value), ...(datatype ? { datatype } : {}), ...(lang ? { lang } : {}) });
const RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#', RDFS = 'http://www.w3.org/2000/01/rdf-schema#';
const GSP = 'http://www.opengis.net/ont/geosparql#', XSD = 'http://www.w3.org/2001/XMLSchema#';
const SKOS = 'http://www.w3.org/2004/02/skos/core#', WGS = 'http://www.w3.org/2003/01/geo/wgs84_pos#';
const toWkt = (g) => g.type === 'Point' ? `POINT(${g.coordinates.join(' ')})`
  : g.type === 'LineString' ? `LINESTRING(${g.coordinates.map((c) => c.join(' ')).join(', ')})`
  : `POLYGON((${g.coordinates[0].map((c) => c.join(' ')).join(', ')}))`;

function describeOut(iri) {
  const t = [];
  const add = (p, o) => t.push({ s: iri, p, o });
  const f = byIri.get(iri);
  if (f) {
    const cls = CLASSES.find((c) => c.class === f.class);
    add(`${RDF}type`, uri(cls.class_iri));
    add(`${RDFS}label`, lit(f.label));
    add(`${ERA}inCountry`, uri(f.facets.country[0].value));
    add(`${ERA}infrastructureManager`, uri(f.facets.im[0].value));
    add(`${GSP}hasGeometry`, uri(`${iri}/geometry`));
    if (f.class === 'operational-point') {
      add(`${ERA}uopid`, lit(f.id));
      add(`${ERA}opType`, uri(f.facets['op-type'][0].value));
      add(`${WGS}lat`, lit(f.geometry.coordinates[1].toFixed(6), `${XSD}double`));
      add(`${WGS}long`, lit(f.geometry.coordinates[0].toFixed(6), `${XSD}double`));
      add(`${ERA}opName`, lit(f.label, null, f.country === 'BEL' ? 'nl' : 'nl'));
    }
    if (f.class === 'section-of-line') {
      add(`${ERA}opStart`, uri(f.opStart.iri));
      add(`${ERA}opEnd`, uri(f.opEnd.iri));
      add(`${ERA}length`, lit(f.facets.length[0].value, `${XSD}double`));
      add(`${ERA}maximumPermittedSpeed`, lit(f.facets['max-speed'][0].value, `${XSD}integer`));
      add(`${ERA}hasElectrification`, lit(f.facets.electrified[0].value, `${XSD}boolean`));
    }
    if (f.class === 'tunnel') {
      add(`${ERA}length`, lit(f.facets['tunnel-length'][0].value, `${XSD}double`));
      add(`${ERA}hasEmergencyPlan`, lit(f.facets['emergency-plan'][0].value, `${XSD}boolean`));
      add(`${ERA}isPartOf`, uri(f.sol.iri));
    }
    if (f.class === 'switch') add(`${ERA}switchType`, uri(f.facets['switch-type'][0].value));
    if (f.class === 'signal') { add(`${ERA}signalType`, uri(f.facets['signal-type'][0].value)); add(`${ERA}signalOrientation`, uri(`${ERA}concepts/orientation/normal`)); }
    if (f.class === 'platform') add(`${ERA}platformHeight`, lit(f.facets['platform-height'][0].value, `${XSD}integer`));
    if (f.op) add(`${ERA}inOperationalPoint`, uri(f.op.iri));
    add('http://purl.org/dc/terms/modified', lit('2026-09-30T08:00:00Z', `${XSD}dateTime`));
    add(`${ERA}notes`, { type: 'bnode', value: `b${f.id}` });
  } else if (iri.endsWith('/geometry') && byIri.has(iri.slice(0, -9))) {
    add(`${RDF}type`, uri(`${GSP}Geometry`));
    add(`${GSP}asWKT`, lit(toWkt(byIri.get(iri.slice(0, -9)).geometry), `${GSP}wktLiteral`));
  } else if (labels[iri]) {
    add(`${RDF}type`, uri(iri.startsWith(COUNTRY) ? `${SKOS}Concept` : iri.includes('/organisations/') ? `${ERA}Body` : `${SKOS}Concept`));
    add(`${SKOS}prefLabel`, lit(labels[iri], null, 'en'));
    if (iri.includes('/concepts/')) add(`${SKOS}inScheme`, uri(iri.replace(/\/[^/]+$/, '')));
  } else if (iri.startsWith(ERA) && /^[A-Z]/.test(iri.slice(ERA.length))) {
    add(`${RDF}type`, uri('http://www.w3.org/2002/07/owl#Class'));
    add(`${RDFS}label`, lit(iri.slice(ERA.length).replace(/([a-z])([A-Z])/g, '$1 $2'), null, 'en'));
  }
  return t;
}
function describeIn(iri) {
  const t = [];
  for (const f of features) {
    for (const tr of describeOut(f.iri)) if (tr.o.type === 'uri' && tr.o.value === iri) t.push(tr);
    if (t.length >= 200) break;
  }
  return t;
}
function labelsFor(triples) {
  const out = {};
  for (const tr of triples) for (const v of [tr.s, tr.o.type === 'uri' ? tr.o.value : null]) {
    if (!v) continue;
    if (byIri.has(v)) out[v] = byIri.get(v).label;
    else if (labels[v]) out[v] = labels[v];
    else if (v.endsWith('/geometry')) out[v] = 'Geometry';
  }
  for (const c of CLASSES) out[c.class_iri] = c.label;
  return out;
}

// ---------- HTTP ----------
const send = (res, status, body) => {
  const data = body === undefined ? '' : JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'access-control-allow-origin': '*' });
  res.end(data);
};
const readJson = (req) => new Promise((resolve, reject) => {
  let b = '';
  req.on('data', (c) => (b += c));
  req.on('end', () => { try { resolve(b ? JSON.parse(b) : {}); } catch (e) { reject(e); } });
});
const ENVS = [
  { key: 'prod', label: 'PROD', sparql: 'https://data-interop.era.europa.eu/api/sparql', default: true },
  { key: 'uat', label: 'UAT', sparql: 'https://uat.data-interop.era.europa.eu/api/sparql', default: false },
];
const checkEnv = (env) => ENVS.some((e) => e.key === env);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const p = url.pathname;
  const env = url.searchParams.get('env');
  try {
    if (req.method === 'OPTIONS') { res.writeHead(204, { 'access-control-allow-origin': '*', 'access-control-allow-headers': 'content-type' }); return res.end(); }
    if (p === '/api/envs') return send(res, 200, ENVS);
    if (p === '/api/meta') {
      if (!checkEnv(env)) return send(res, 400, { detail: 'unknown env' });
      const classes = CLASSES.map((c) => ({ min_zoom: 0, ...c, count: features.filter((f) => f.class === c.class).length }));
      const qa = ['BEL', 'NLD'].map((c) => qaCounts(`${ERA}graph/rinf/${c}`));
      const quality = { enabled: true, validated: 2, violations: qa.reduce((a, q) => a + q.violations, 0), warnings: qa.reduce((a, q) => a + q.warnings, 0),
        infos: qa.reduce((a, q) => a + q.infos, 0), too_large: 1, checked_at: '2026-10-13T03:40:00Z', shapes_graph: SHAPES_SRC[env].graph,
        shapes_loaded_at: '2026-10-13T03:05:00Z', shapes: SHAPES_SRC[env], shapes_error: null, max_triples: MAX_TRIPLES,
        shacl_report_url: 'https://shacl-report.matdata.eu/' };
      return send(res, 200, { classes, facets: FACETS, datasets: { total: 4, loaded: env === 'uat' ? 3 : 4, failed: env === 'uat' ? 1 : 0, pending: 0, last_ingest: '2026-10-13T03:12:00Z', last_check: '2026-10-14T09:30:00Z' }, quality });
    }
    if (req.method === 'POST' && ['/api/facets', '/api/search', '/api/extent'].includes(p)) {
      const st = await readJson(req);
      if (!checkEnv(st.env)) return send(res, 400, { detail: 'unknown env' });
      await sleep(60);
      if (p === '/api/facets') return send(res, 200, facets(st));
      const hits = features.filter((f) => matches(f, st));
      if (p === '/api/search') {
        hits.sort((a, b) => a.label.localeCompare(b.label));
        const limit = Math.min(Number(st.limit ?? 50), 5000), offset = Number(st.offset ?? 0);
        const items = hits.slice(offset, offset + limit).map((f) => {
          const [lon, lat] = repPoint(f.geometry);
          return { id: f.id, iri: f.iri, class: f.class, label: f.label, lon, lat, qa: qaLevel(f) };
        });
        return send(res, 200, { total: hits.length, items });
      }
      if (!hits.length) return send(res, 200, { bbox: null });
      const all = hits.flatMap((f) => coordsOf(f.geometry));
      const xs = all.map((c) => c[0]), ys = all.map((c) => c[1]);
      return send(res, 200, { bbox: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] });
    }
    if (p === '/api/export') {
      // CSV only (the mock has no XLSX writer); good enough to exercise the download.
      const st = JSON.parse(Buffer.from(url.searchParams.get('state') || '', 'base64url').toString() || '{}');
      if (url.searchParams.get('format') === 'ttl') {
        const body = features.filter((f) => matches(f, st)).map((f) => `<${f.iri}> <http://www.w3.org/2000/01/rdf-schema#label> ${JSON.stringify(f.label)} .`);
        res.writeHead(200, { 'content-type': 'text/turtle; charset=utf-8', 'content-disposition': 'attachment; filename="rinf-atlas-mock.ttl"' });
        return res.end(body.join('\n') + '\n');
      }
      const q = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
      const rows = features.filter((f) => matches(f, st)).map((f) => [f.iri, f.label, f.class, ...repPoint(f.geometry)].map(q).join(','));
      res.writeHead(200, { 'content-type': 'text/csv; charset=utf-8', 'content-disposition': 'attachment; filename="rinf-atlas-mock.csv"' });
      return res.end(['IRI,Label,Class,Longitude,Latitude', ...rows].join('\r\n'));
    }
    if (/^\/api\/tiles\/\d+\/\d+\/\d+\.pbf$/.test(p)) { res.writeHead(204); return res.end(); }
    if (p === '/api/feature') {
      const id = url.searchParams.get('id');
      const f = id != null ? features.find((x) => String(x.id) === id) : byIri.get(url.searchParams.get('iri') || '');
      if (!f) return send(res, 404, { detail: 'feature not found' });
      const facetsOut = Object.fromEntries(Object.entries(f.facets).filter(([k]) => k !== 'quality').map(([k, vs]) => [k, vs.map(({ value, label }) => ({ value, label }))]));
      const status = qaStatus(f.graph);
      const quality = { status, checked_at: status ? '2026-10-13T03:40:00Z' : null,
        note: status === 'skipped' ? 'not validated: too large (18,400,000 triples > limit 15,000,000)' : null,
        reason: status === 'skipped' ? 'too_large' : null, shapes: status ? SHAPES_SRC[env] : null,
        results: status === 'ok' ? f.qaResults.map(({ sev, ...r }) => r) : [], truncated: false, report: status === 'ok', html: status === 'ok' };
      return send(res, 200, { iri: f.iri, label: f.label, classes: [f.class], graph_iri: f.graph, facets: facetsOut, geometry: f.geometry, quality });
    }
    if (p === '/api/describe') {
      const iri = url.searchParams.get('iri') || '';
      const dir = url.searchParams.get('direction') || 'out';
      await sleep(250); // it's "live" after all
      const triples = dir === 'in' ? describeIn(iri) : describeOut(iri);
      return send(res, 200, { iri, triples, labels: labelsFor(triples) });
    }
    if (p === '/api/datasets') {
      const providers = ['Infrabel', 'ProRail', 'CFL', 'DB InfraGO'];
      const rows = ['BEL', 'NLD', 'LUX', 'DEU'].map((c, i) => {
        const graph_iri = `${ERA}graph/rinf/${c}`;
        const failed = env === 'uat' && i === 3;
        const file = `RINF_${c}_2026-09-30.ttl`;
        return {
          graph_iri, title: file, provider: providers[i], country: c, modified: '2026-09-30T08:00:00Z',
          status: failed ? 'failed' : 'loaded', triples: c === 'LUX' ? 18400000 : Math.round(1e6 * (i + 1) * 1.7),
          ingested_at: failed ? null : '2026-10-13T03:12:00Z',
          error: failed ? 'HTTP 503 from the ERA repository while downloading' : null,
          dataset_id: `mock${i}`, file_name: file,
          features: failed ? 0 : features.filter((f) => f.graph === graph_iri).length,
          quality: failed ? { status: null, violations: null, warnings: null, infos: null, focus_nodes: null, kept: null, checked_at: null, seconds: null, note: null, reason: null, limit: null, shapes: null, report: false, html: false }
            : (() => {
              const st = qaStatus(graph_iri), q = qaCounts(graph_iri), ok = st === 'ok';
              return { status: st, violations: ok ? q.violations : null, warnings: ok ? q.warnings : null, infos: ok ? q.infos : null,
                focus_nodes: ok ? q.focus : null, kept: ok ? q.violations + q.warnings + q.infos : null, checked_at: st ? '2026-10-13T03:40:00Z' : null,
                seconds: ok ? 42.5 : null, note: st === 'skipped' ? 'not validated: too large (18,400,000 triples > limit 15,000,000)' : null,
                reason: st === 'skipped' ? 'too_large' : null, limit: st === 'skipped' ? MAX_TRIPLES : null,
                shapes: st ? SHAPES_SRC[env] : null, report: ok, html: ok };
            })(),
        };
      });
      rows.push({ graph_iri: `${ERA}graph/rinf/AUT`, title: 'RINF_AUT_2025.ttl', provider: 'ÖBB-Infrastruktur', country: 'AUT',
        modified: '2025-12-01T08:00:00Z', status: 'removed', triples: 0, ingested_at: '2026-01-02T03:00:00Z', error: null,
        dataset_id: 'mock9', file_name: 'RINF_AUT_2025.ttl', features: 0,
        quality: { status: null, violations: null, warnings: null, infos: null, focus_nodes: null, kept: null, checked_at: null, seconds: null, note: null, reason: null, limit: null, shapes: null, report: false, html: false } });
      return send(res, 200, rows);
    }
    if (p === '/api/quality/summary') {
      if (!checkEnv(env)) return send(res, 400, { detail: 'unknown env' });
      const graph = url.searchParams.get('graph') || '';
      const counts = new Map();
      for (const f of features) if (f.graph === graph && qaStatus(graph) === 'ok') for (const r of f.qaResults) {
        const k = `${r.shape} ${r.component}`;
        const cur = counts.get(k) || { shape: r.shape, component: r.component, severity: r.severity, count: 0, message: r.message, sev: r.sev };
        cur.count++; counts.set(k, cur);
      }
      await sleep(120);
      return send(res, 200, [...counts.values()].sort((a, b) => b.sev - a.sev || b.count - a.count).map(({ sev, ...r }) => r));
    }
    if (p === '/api/quality/report') {
      const graph = url.searchParams.get('graph') || '';
      if (qaStatus(graph) !== 'ok') return send(res, 404, { detail: 'no validation report for this dataset' });
      if (url.searchParams.get('format') === 'html') {
        res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'content-security-policy': 'sandbox allow-scripts' });
        return res.end(`<!doctype html><meta charset="utf-8"><title>SHACL report (mock)</title><body style="font-family:sans-serif"><h1>Mock shacl-report viewer</h1><p>The real API serves the HTML page shacl-report generated for this dataset.</p><pre>${reportTurtle(graph).replace(/[<&]/g, (c) => (c === '<' ? '&lt;' : '&amp;'))}</pre>`);
      }
      res.writeHead(200, { 'content-type': 'text/turtle; charset=utf-8', 'content-disposition': 'attachment; filename="rinf-atlas-mock-shacl-report.ttl"' });
      return res.end(reportTurtle(graph));
    }
    if (p === '/api/quality/shapes') {
      const src = SHAPES_SRC[env] ?? SHAPES_SRC.prod;
      res.writeHead(200, { 'content-type': 'text/turtle; charset=utf-8', 'content-disposition': `attachment; filename="era-shacl-shapes-${env}-${src.tag ?? src.version}.ttl"` });
      return res.end(`# SHACL shapes used by RINF Atlas for data quality: ${src.label}\n# Source: ${src.url}\n# Version (owl:versionInfo): ${src.version}\n\n@prefix sh: <http://www.w3.org/ns/shacl#> .\n@prefix era: <${ERA}> .\n\n<${SHAPES}OperationalPointShape> a sh:NodeShape ; sh:targetClass era:OperationalPoint ;\n  sh:property [ sh:path era:uopid ; sh:minCount 1 ; sh:maxCount 1 ] .\n`);
    }
    send(res, 404, { detail: 'not found' });
  } catch (e) {
    send(res, 500, { detail: String(e) });
  }
});
server.listen(PORT, () => {
  const counts = CLASSES.map((c) => `${c.class}=${features.filter((f) => f.class === c.class).length}`).join(' ');
  console.log(`RINF Atlas mock API on http://localhost:${PORT}/api  (${features.length} features: ${counts})`);
});
