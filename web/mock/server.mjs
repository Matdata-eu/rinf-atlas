// RINF Atlas mock API (plain Node http, no dependencies).
// Implements every endpoint of docs/api.md with deterministic fake data
// for Belgium and the Netherlands (a few national lines with running tracks
// for the line profile). Listens on PORT (default 8000).
import fs from 'node:fs';
import http from 'node:http';
import { URL } from 'node:url';

const PORT = Number(process.env.PORT || 8000);
const ERA = 'http://data.europa.eu/949/';
const FI = `${ERA}functionalInfrastructure/`;
const COUNTRY = 'http://publications.europa.eu/resource/authority/country/';

// The repository's own facet shapes, served by /api/shapes.
const SHAPES_DIR = new URL('../../shapes/', import.meta.url);
const shapeFiles = () => { try { return fs.readdirSync(SHAPES_DIR).filter((n) => n.endsWith('.ttl')).sort(); } catch { return []; } };

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
  // Running tracks share their section of line's geometry; the map colours a section by its tracks' values.
  { class: 'running-track', class_iri: `${ERA}RunningTrack`, label: 'Running track', color: '#2ca02c', geometry: 'LineString', min_zoom: 10 },
  { class: 'tunnel', class_iri: `${ERA}Tunnel`, label: 'Tunnel', color: '#9a6324', geometry: 'LineString' },
  { class: 'switch', class_iri: `${ERA}Switch`, label: 'Switch', color: '#3cb44b', geometry: 'Point', min_zoom: 11 },
  { class: 'signal', class_iri: `${ERA}Signal`, label: 'Signal', color: '#f58231', geometry: 'Point', min_zoom: 11 },
  // Defined by the shapes but nothing indexed: the UI hides it.
  { class: 'etcs-level', class_iri: `${ERA}ETCSLevel`, label: 'ETCS level', color: '#808000', geometry: 'Point' },
  { class: 'platform', class_iri: `${ERA}PlatformEdge`, label: 'Platform edge', color: '#911eb4', geometry: 'Polygon' },
];
const FACETS = [
  { key: 'country', class: '*', label: 'Country', kind: 'terms', datatype: 'iri', position: 1, color_by: 30 },
  { key: 'im', class: '*', label: 'Infrastructure manager', kind: 'terms', datatype: 'iri', position: 2, color_by: 31 },
  { key: 'op-type', class: 'operational-point', label: 'Operational point type', kind: 'terms', datatype: 'iri', position: 10, color_by: 20 },
  { key: 'uopid', class: 'operational-point', label: 'Primary location code (UOPID)', kind: 'terms', datatype: 'string', position: 11 },
  { key: 'length', class: 'section-of-line', label: 'Length', kind: 'range', datatype: 'xsd:double', position: 20, unit: 'M' },
  { key: 'max-speed', class: 'section-of-line', label: 'Maximum speed', kind: 'range', datatype: 'xsd:integer', position: 21, unit: 'KiloM-PER-HR' },
  { key: 'electrified', class: 'section-of-line', label: 'Electrified', kind: 'boolean', datatype: 'xsd:boolean', position: 22 },
  { key: 'track-speed', class: 'running-track', label: 'Maximum permitted speed', kind: 'range', datatype: 'xsd:integer', position: 23, unit: 'KiloM-PER-HR', color_by: 1 },
  { key: 'energy-supply', class: 'running-track', label: 'Energy supply system', kind: 'terms', datatype: 'iri', position: 24, color_by: 2 },
  { key: 'etcs-level', class: 'running-track', label: 'ETCS level', kind: 'terms', datatype: 'iri', position: 25, color_by: 3 },
  { key: 'gauge', class: 'running-track', label: 'Nominal track gauge', kind: 'terms', datatype: 'iri', position: 26, color_by: 4 },
  { key: 'level-crossings', class: 'running-track', label: 'Existence of level crossings', kind: 'boolean', datatype: 'xsd:boolean', position: 27 },
  { key: 'nationalLine.lineId', class: 'section-of-line', label: 'Line identifier (national line identification)', kind: 'terms', datatype: 'string', position: 19 },
  { key: 'lengthOfSectionOfLine', class: 'section-of-line', label: 'Length of section of line', kind: 'range', datatype: 'xsd:double', position: 23, unit: 'KiloM' },
  { key: 'trackDirection', class: 'running-track', label: 'Normal running direction', kind: 'terms', datatype: 'iri', position: 24 },
  { key: 'lineCategory', class: 'running-track', label: 'Category of line', kind: 'terms', datatype: 'iri', position: 25 },
  { key: 'maximumPermittedSpeed', class: 'running-track', label: 'Maximum permitted speed', kind: 'range', datatype: 'xsd:integer', position: 26, unit: 'KiloM-PER-HR' },
  { key: 'gaugingProfile', class: 'running-track', label: 'Gauging', kind: 'terms', datatype: 'iri', position: 27 },
  { key: 'wheelSetGauge', class: 'running-track', label: 'Nominal track gauge', kind: 'terms', datatype: 'iri', position: 28 },
  { key: 'contactLineSystem.contactLineSystemType', class: 'running-track', label: 'Type of contact line system', kind: 'terms', datatype: 'iri', position: 29 },
  { key: 'contactLineSystem.energySupplySystem', class: 'running-track', label: 'Energy supply system (Voltage and frequency) (contact line system)', kind: 'terms', datatype: 'iri', position: 30 },
  { key: 'etcs.etcsLevelType', class: 'running-track', label: 'European Train Control System (ETCS) level', kind: 'terms', datatype: 'iri', position: 31 },
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
const exportJobs = new Map(); // mock export jobs (/api/exports)
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
// Running tracks: one or two per section of line, with their own RNG so the other mock data stay as they were.
{
  let s2 = 7;
  const r2 = () => ((s2 = (s2 * 1664525 + 1013904223) >>> 0) / 4294967296);
  const p2 = (arr) => arr[Math.floor(r2() * arr.length)];
  const ENERGY = { BEL: [['DC3000', 'DC 3 kV', 6], ['AC25', 'AC 25 kV 50 Hz', 2], ['none', 'Not electrified', 1]],
    NLD: [['DC1500', 'DC 1.5 kV', 6], ['AC25', 'AC 25 kV 50 Hz', 1], ['none', 'Not electrified', 1]] };
  const ETCS = [['N', 'No ETCS'], ['N', 'No ETCS'], ['1', 'ETCS level 1'], ['2', 'ETCS level 2']];
  const weighted = (opts) => { const tot = opts.reduce((a, o) => a + o[2], 0); let x = r2() * tot; for (const o of opts) { x -= o[2]; if (x < 0) return o; } return opts[0]; };
  let rt = 0;
  for (const sol of sols) {
    const n = r2() < 0.6 ? 2 : 1;
    const base = sol.facets['max-speed'][0].value;
    const [ek, el] = weighted(ENERGY[sol.country]);
    for (let i = 0; i < n; i++) {
      const id = `RT${String(++rt).padStart(5, '0')}`;
      const speed = i === 0 ? base : p2([base, base, Math.max(40, base - 40)]);
      const [ck, cl] = p2(ETCS);
      // A few tracks on the same section differ in electrification, so some sections are "mixed".
      const [ek2, el2] = i === 1 && r2() < 0.12 ? weighted(ENERGY[sol.country]) : [ek, el];
      const facets = { country: sol.facets.country, im: sol.facets.im,
        'energy-supply': [term(`${ERA}concepts/energy-supply-systems/${ek2}`, el2)],
        gauge: [term(`${ERA}concepts/nominal-track-gauges/${r2() < 0.97 ? '30' : '40'}`, 'x')],
        'level-crossings': [(() => { const v = r2() < 0.4; return { value: v, label: v ? 'yes' : 'no' }; })()] };
      facets.gauge[0].label = labels[facets.gauge[0].value] = facets.gauge[0].value.endsWith('30') ? '1435 mm' : '1668 mm';
      if (r2() < 0.85) facets['track-speed'] = [{ value: speed, label: String(speed) }];
      if (r2() < 0.75) facets['etcs-level'] = [term(`${ERA}concepts/etcs-levels/${ck}`, cl)];
      features.push({ id, iri: `${ERA}track/${id}`, class: 'running-track', label: `${sol.label} track ${i + 1}`,
        geometry: sol.geometry, country: sol.country, sol, parent: sol, facets });
    }
  }
}
// ---------- national lines + their running tracks (for the line profile) ----------
// Each line runs through the main operational points of the listed towns, with an
// intermediate junction between two towns. Line 161 has a missing section (a gap).
const LINES = [
  ['50A', 'BEL', ['Brussel-Zuid', 'Aalst', 'Gent-Sint-Pieters', 'Brugge', 'Oostende'], 160],
  ['36', 'BEL', ['Brussel-Noord', 'Leuven', 'Landen', 'Liège-Guillemins'], 200],
  ['25', 'BEL', ['Brussel-Centraal', 'Mechelen', 'Antwerpen-Centraal'], 140],
  ['161', 'BEL', ['Brussel-Zuid', 'Ottignies', 'Namur', 'Libramont', 'Arlon'], 130],
  ['Asd-Vl', 'NLD', ['Amsterdam Centraal', 'Utrecht Centraal', '\'s-Hertogenbosch', 'Eindhoven Centraal', 'Venlo'], 140],
];
const C = (k, l) => term(`${ERA}concepts/${k}`, l);
const DIR = { N: C('track-running-directions/rinf/10', 'Normal'), O: C('track-running-directions/rinf/20', 'Opposite'), B: C('track-running-directions/rinf/30', 'Both directions') };
const OCL = C('contact-line-systems/rinf/10', 'Overhead contact line (OCL)');
const NOT_ELEC = C('contact-line-systems/rinf/30', 'Not electrified');
const ENERGY = { BEL: C('energy-supply-systems/rinf/DC3', 'DC 3kV'), NLD: C('energy-supply-systems/rinf/DC15', 'DC 1.5kV'), HS: C('energy-supply-systems/rinf/AC25', 'AC 25kV-50Hz') };
const ETCS = [C('etcs-levels/rinf/NOETCS', 'No ETCS'), C('etcs-levels/rinf/1', 'ETCS level 1'), C('etcs-levels/rinf/2', 'ETCS level 2')];
const GAUGING = [C('gauging-profiles/rinf/GB', 'GB'), C('gauging-profiles/rinf/GC', 'GC'), C('gauging-profiles/rinf/G1', 'G1')];
const CATEGORY = [C('load-capability-line-categories/rinf/D4', 'D4'), C('load-capability-line-categories/rinf/C4', 'C4'), C('load-capability-line-categories/rinf/E4', 'E4')];
const GAUGE = C('nominal-track-gauges/rinf/30', '1435');
const lineSols = [];
const opByLabel = new Map(ops.map((o) => [o.label, o]));
let trn = 0;
for (const [line, c, towns, vmax] of LINES) {
  let km = 0;
  for (let t = 0; t < towns.length - 1; t++) {
    const A = opByLabel.get(towns[t]), B = opByLabel.get(towns[t + 1]);
    const [x1, y1] = A.geometry.coordinates, [x2, y2] = B.geometry.coordinates;
    const f = between(0.35, 0.65);
    const mid = [x1 + (x2 - x1) * f + between(-0.03, 0.03), y1 + (y2 - y1) * f + between(-0.03, 0.03)];
    km += Math.round(dist(A, B) / 1000 * f);
    const id = `${c === 'BEL' ? 'BE' : 'NL'}${String(++opn).padStart(5, '0')}`;
    const J = { id, iri: `${FI}operationalPoints/${id}`, class: 'operational-point', label: `Aansluiting ${line}/${km}`,
      geometry: { type: 'Point', coordinates: mid }, country: c,
      facets: { country: [countryTerm(c)], im: [imTerm(c)], 'op-type': [term(`${ERA}concepts/op-types/rinf/80`, 'junction')], uopid: [{ value: id, label: id }] } };
    ops.push(J); features.push(J);
    for (const [P, Q] of [[A, J], [J, B]]) {
      if (line === '161' && P === J && t === 2) continue; // missing section: a gap in the line
      const key = `L${line}_${P.id}_${Q.id}`;
      const len = +(dist(P, Q) * 1.08 / 1000).toFixed(3);
      // some sections are stored the other way round (opStart/opEnd swapped)
      const [S, E] = rnd() < 0.3 ? [Q, P] : [P, Q];
      const [sx, sy] = S.geometry.coordinates, [ex, ey] = E.geometry.coordinates;
      const sol = {
        id: `SOL_${key}`, iri: `${FI}sectionsOfLine/${key}`, class: 'section-of-line', label: `${S.label} – ${E.label}`,
        geometry: { type: 'LineString', coordinates: [[sx, sy], [(sx + ex) / 2 + between(-0.01, 0.01), (sy + ey) / 2 + between(-0.01, 0.01)], [ex, ey]] },
        country: c, opStart: S, opEnd: E,
        facets: {
          country: [countryTerm(c)], im: [imTerm(c)],
          'nationalLine.lineId': [{ value: line, label: line }],
          length: [{ value: Math.round(len * 1000), label: String(Math.round(len * 1000)) }],
          lengthOfSectionOfLine: [{ value: len, label: String(len) }],
        },
      };
      lineSols.push(sol); sols.push(sol); features.push(sol);
      // tracks: mostly double track (one per direction), sometimes single track
      const near = P === J ? Q : P; // the town end: slower
      const base = Math.min(vmax, pick([vmax, vmax, vmax - 20, near.label.includes('Brussel') ? 90 : vmax]));
      const elec = !(line === '161' && t === 3);
      const etcs = line === '36' ? ETCS[2] : pick(ETCS);
      const dirs = rnd() < 0.8 ? ['N', 'O'] : ['B'];
      dirs.forEach((d, n) => {
        const tid = `TR${String(++trn).padStart(5, '0')}`;
        const speed = n === 1 && rnd() < 0.3 ? base - 20 : base;
        features.push({
          id: tid, iri: `${FI}tracks/${tid}`, class: 'running-track', label: `Track ${n + 1} ${sol.label}`,
          geometry: sol.geometry, country: c, sol, parent: sol, // part of the section (feature_link in the real index)
          facets: {
            country: sol.facets.country, im: sol.facets.im,
            trackDirection: [DIR[d]],
            maximumPermittedSpeed: [{ value: speed, label: String(speed) }],
            'contactLineSystem.contactLineSystemType': [elec ? OCL : NOT_ELEC],
            ...(elec ? { 'contactLineSystem.energySupplySystem': [line === '36' && t === 2 ? ENERGY.HS : ENERGY[c]] } : {}),
            wheelSetGauge: [GAUGE],
            'etcs.etcsLevelType': [etcs],
            gaugingProfile: [line === '25' ? GAUGING[1] : pick(GAUGING)],
            lineCategory: [pick(CATEGORY)],
          },
        });
      });
    }
  }
}
// Start / end operational point of every section (link facets: shown in the detail panel, never filters).
for (const s of sols) {
  s.facets.opStart = [{ value: s.opStart.iri, label: s.opStart.label }];
  s.facets.opEnd = [{ value: s.opEnd.iri, label: s.opEnd.label }];
}
// One operational point with a point and a station-area polygon, stored like the real API stores
// several WKT literals: a GeometryCollection (the OGC API splits it over two layers).
{
  const [lon, lat] = [4.3, 50.7];
  features.push({
    id: 'BE99999', iri: `${FI}operationalPoints/BE99999`, class: 'operational-point', label: 'Halle-Vorming (point + area)', country: 'BEL',
    geometry: { type: 'GeometryCollection', geometries: [{ type: 'Point', coordinates: [lon, lat] },
      { type: 'Polygon', coordinates: [[[lon - 0.01, lat - 0.005], [lon + 0.01, lat - 0.005], [lon + 0.01, lat + 0.005], [lon - 0.01, lat + 0.005], [lon - 0.01, lat - 0.005]]] }] },
    facets: { country: [countryTerm('BEL')], im: [imTerm('BEL')], 'op-type': [term(`${ERA}concepts/op-types/rinf/100`, 'shunting yard')],
      uopid: [{ value: 'BE99999', label: 'BE99999' }] },
  });
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
const coordsOf = (g) => (g.type === 'GeometryCollection' ? g.geometries.flatMap(coordsOf)
  : g.type === 'Point' ? [g.coordinates] : g.type === 'LineString' ? g.coordinates : g.coordinates.flat());
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
  const unlocated_by_class = {};
  for (const f of features) if (matches(f, st, { skipClasses: true })) {
    by_class[f.class]++;
    if (!f.geometry) unlocated_by_class[f.class] = (unlocated_by_class[f.class] || 0) + 1;
  }
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
  return { total: base.length, by_class, unlocated_by_class, facets: out };
}

// ---------- map colouring (POST /api/theme, `color` on search) ----------
const MIXED = '__mixed__';
const themes = new Map();
const nice = (x) => { if (!x) return x; const d = Math.floor(Math.log10(Math.abs(x))) - 1; return Math.round(x / 10 ** d) * 10 ** d; };
function theme(key) {
  if (themes.has(key)) return themes.get(key);
  const [scope, fk] = key.includes('/') ? key.split('/', 2) : [null, key];
  const fdef = FACETS.find((f) => f.key === fk && (!scope || f.class === scope));
  if (!fdef) return null;
  const owners = fdef.class === '*' ? null : [fdef.class];
  // Sections of line take the values of their running tracks (they share their geometry).
  const rollup = owners && owners.includes('running-track') ? ['section-of-line'] : [];
  const vals = new Map(); // feature -> raw values
  for (const f of features) {
    if (owners && !owners.includes(f.class)) continue;
    const vs = (f.facets[fk] || []).map((v) => v.value);
    if (!vs.length) continue;
    vals.set(f, [...(vals.get(f) || []), ...vs]);
    if (rollup.length && f.parent) vals.set(f.parent, [...(vals.get(f.parent) || []), ...vs]);
  }
  const v = new Map();
  for (const [f, vs] of vals) {
    if (fdef.kind === 'range') v.set(f, Math.max(...vs));
    else { const u = [...new Set(vs.map(String))]; v.set(f, u.length > 1 ? MIXED : u[0]); }
  }
  const t = { key, fdef, owners, rollup, v };
  if (fdef.kind === 'range') {
    const ns = [...v.values()].sort((a, b) => a - b);
    const distinct = [...new Set(ns)];
    t.lo = ns[0] ?? null; t.hi = ns[ns.length - 1] ?? null;
    t.discrete = distinct.length <= 5;
    t.breaks = t.discrete ? distinct.slice(1) : [0.2, 0.4, 0.6, 0.8].map((q) => nice(ns[Math.floor(q * (ns.length - 1))]))
      .filter((b, i, a) => b > t.lo && b <= t.hi && (i === 0 || b > a[i - 1]));
  } else {
    const counts = new Map();
    for (const x of v.values()) if (x !== MIXED) counts.set(x, (counts.get(x) || 0) + 1);
    t.slots = fdef.kind === 'boolean' ? ['true', 'false'] : [...counts].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0]))).slice(0, 8).map((x) => x[0]);
  }
  themes.set(key, t);
  return t;
}
function themeLegend(st, key) {
  const t = theme(key);
  if (!t) return null;
  const col = t.owners ? [...t.owners, ...t.rollup] : null;
  const hits = features.filter((f) => matches(f, st) && (!col || col.includes(f.class)));
  const out = { key, kind: t.fdef.kind, label: t.fdef.label, unit: t.fdef.unit ?? null, classes: t.owners ?? [], rollup: t.rollup };
  let nodata = 0;
  if (t.fdef.kind === 'range') {
    const n = t.lo == null ? 0 : (t.discrete ? t.breaks.length + 1 : t.breaks.length + 1);
    const counts = new Array(n).fill(0);
    for (const f of hits) { const x = t.v.get(f); if (x == null) nodata++; else counts[t.breaks.filter((b) => x >= b).length]++; }
    const edges = t.discrete ? [t.lo, ...t.breaks] : [t.lo, ...t.breaks, t.hi];
    const bins = t.lo == null ? [] : counts.map((c, i) => ({ min: edges[i], max: t.discrete ? edges[i] : edges[i + 1], count: c }));
    return { ...out, breaks: t.breaks, bins, min: t.lo, max: t.hi, discrete: t.discrete, nodata, total: hits.length };
  }
  const counts = new Map();
  for (const f of hits) { const x = t.v.get(f); if (x == null) nodata++; else counts.set(x, (counts.get(x) || 0) + 1); }
  const others = [...counts].filter(([x]) => x !== MIXED && !t.slots.includes(x)).sort((a, b) => b[1] - a[1]);
  const lab = (x) => (x === 'true' ? 'Yes' : x === 'false' ? 'No' : labels[x] ?? String(x));
  const values = [...t.slots.map((x) => [x, counts.get(x) || 0]), ...others]
    .map(([x, c]) => ({ value: x, label: lab(x), count: c, slot: t.slots.includes(x) ? t.slots.indexOf(x) : null }));
  return { ...out, values, more: false, other: others.reduce((a, x) => a + x[1], 0), mixed: counts.get(MIXED) || 0, nodata, total: hits.length };
}

// ---------- vehicle compatibility ----------
// The real rules live in shapes/compatibility.ttl; the mock gives each section of line one
// or two synthetic running tracks (derived from its id, so the main RNG is untouched).
const CC = `${ERA}concepts/`;
const ct = (scheme, code, label) => term(`${CC}${scheme}/${code}`, label);
const CV = {
  g1435: ct('nominal-track-gauges', '30', '1435'),
  ac25: ct('energy-supply-systems', 'AC10', 'AC 25kV-50Hz'), dc3: ct('energy-supply-systems', 'DC30', 'DC 3kV'),
  dc15: ct('energy-supply-systems', 'DC40', 'DC 1.5kV'),
  ep: ct('compliant-pantograph-heads', '20', '1600 mm (EP)'), be: ct('other-pantograph-heads', '40', '1760 mm (BE)'),
  l1: ct('etcs-levels', '20', '1'), l2: ct('etcs-levels', '30', '2'), ln: ct('etcs-levels', '10', 'N'), l0: ct('etcs-levels', '50', '0'),
  ga: ct('gaugings', '10', 'GA'), gb: ct('gaugings', '20', 'GB'), gc: ct('gaugings', '30', 'GC'), g1: ct('gaugings', '40', 'G1'), g2: ct('gaugings', '60', 'G2'),
  tc: ct('train-detection', '10', 'track circuit'), wd: ct('train-detection', '20', 'wheel detector'),
  d4: ct('load-capability-line-categories', '90', 'D4'), c4: ct('load-capability-line-categories', '60', 'C4'),
};
const COMPAT_RULES = [
  ['wheelSetGauge', 'Nominal track gauge', [`${ERA}wheelSetGauge`], 'hasValue', 'terms', null, ['wheelSetGauge']],
  ['gaugingProfile', 'Gauging profile', [`${ERA}gaugingProfile`], 'hasValue', 'terms', null, ['gaugingProfile']],
  ['energySupplySystem', 'Energy supply system', [`${ERA}energySupplySystem`], 'hasValue', 'terms', null, ['contactLineSystem.energySupplySystem']],
  ['pantographHead', 'Pantograph head', [`${ERA}configDependentParameter`, `${ERA}vehiclePantographHead`], 'hasValue', 'terms', null, ['otherPantographHead', 'tsiPantographHead']],
  ['etcsLevel', 'ETCS level on board', [`${ERA}etcsEquipmentOnBoardLevel`], 'hasValue', 'terms', null, ['etcs.etcsLevelType']],
  ['trainDetectionSystemType', 'Train detection systems', [`${ERA}trainDetectionSystemType`], 'in', 'terms', null, ['trainDetectionSystem.trainDetectionSystemType']],
  ['lineCategory', 'Load capability line categories', [`${ERA}compatibleWithLineCategory`], 'hasValue', 'terms', null, ['trackLoadCapability.loadCapabilityLineCategory']],
  ['axleLoad', 'Axle load', [`${ERA}staticAxleLoadNormalPayload`], 'minInclusive', 'number', 'TONNE', ['trackLoadCapability.loadCapabilityLineCategory']],
  ['massPerMetre', 'Mass per metre', ['https://rinf-atlas.matdata.eu/ns#massPerMetre'], 'minInclusive', 'number', 'TONNE-PER-M', ['trackLoadCapability.loadCapabilityLineCategory']],
  ['trainLength', 'Train length', [`${ERA}lengthOfVehicle`], 'minInclusive', 'number', 'M', ['maxPermittedTrainLength']],
].map(([key, label, path, constraint, kind, unit, facets]) => ({
  key, label, comment: null, path, constraint, kind, datatype: kind === 'number' ? 'http://www.w3.org/2001/XMLSchema#double' : null,
  unit, facets, applies_to: 'track', when_missing: 'unknown', none_value: null, none_from: null, missing_reason: null,
}));
// No contact line: an electric vehicle is not compatible, a bi-mode one ("not electrified") is.
const NOT_ELECTRIFIED = `${CC}contact-line-systems/40`;
Object.assign(COMPAT_RULES.find((r) => r.key === 'energySupplySystem'),
  { when_missing: 'incompatible', none_value: NOT_ELECTRIFIED, missing_reason: 'No contact line' });
Object.assign(COMPAT_RULES.find((r) => r.key === 'pantographHead'),
  { when_missing: 'incompatible', none_from: 'energySupplySystem', missing_reason: 'No contact line' });
// A vehicle without ETCS runs on tracks with no ETCS (N), level 0 or NTC; others require ETCS.
Object.assign(COMPAT_RULES.find((r) => r.key === 'etcsLevel'), {
  without_value: `${CC}etcs-levels/10`, without_label: 'No ETCS installed',
  without_matches: ['10', '50', '60'].map((c) => `${CC}etcs-levels/${c}`) });
// EN 15528 limits of the line categories the mock tracks have (C4, D4).
const LCLC = `${CC}load-capability-line-categories/`;
Object.assign(COMPAT_RULES.find((r) => r.key === 'axleLoad'), { term_values: { [`${LCLC}60`]: 20, [`${LCLC}90`]: 22.5 }, path_factor: 1000 });
Object.assign(COMPAT_RULES.find((r) => r.key === 'massPerMetre'), { term_values: { [`${LCLC}60`]: 8, [`${LCLC}90`]: 8 } });
// The explanation fields of /api/compatibility/rules (infra, source, explanation). The real
// API generates the text from the rule's annotations (api/app/compat.py explain_rule); this
// is a shortened version of it for the mock rules.
const era = (iri) => iri.replace(ERA, 'era:');
const facetPath = (fk) => fk.split('.').map((p) => `era:${p}`).join('/');
function mockSource(r) {
  const lines = [`sh:path ${r.path.length > 1 ? `( ${r.path.map(era).join(' ')} )` : era(r.path[0])}`,
    `atlas:characteristic "${r.key}"`, `rdfs:label "${r.label}"`, `atlas:compareTo ${r.facets.map((f) => `"${f}"`).join(', ')}`,
    `atlas:constraint sh:${r.constraint}`];
  if (r.when_missing === 'incompatible') lines.push('atlas:whenMissing atlas:Incompatible');
  if (r.none_value) lines.push(`atlas:noneValue <${r.none_value}>`);
  if (r.none_from) lines.push(`atlas:noneFrom "${r.none_from}"`);
  if (r.missing_reason) lines.push(`atlas:missingReason "${r.missing_reason}"`);
  if (r.without_value) lines.push(`atlas:withoutValue <${r.without_value}>`, `atlas:withoutLabel "${r.without_label}"`,
    `atlas:withoutMatches ${r.without_matches.map((x) => `<${x}>`).join(', ')}`);
  if (r.unit) lines.push(`atlas:unit "${r.unit}"`);
  return 'PREFIX atlas: <https://rinf-atlas.matdata.eu/ns#>\nPREFIX era: <http://data.europa.eu/949/>\n'
    + 'PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\nPREFIX sh: <http://www.w3.org/ns/shacl#>\n'
    + 'PREFIX shape: <https://rinf-atlas.matdata.eu/shapes/>\n\nshape:vehicle-compatibility sh:property [\n'
    + lines.map((l) => `    ${l} ;`).join('\n') + '\n] .\n';
}
function mockExplain(r) {
  const names = r.facets.map((f) => `“${f}”`).join(' and ');
  const anyNames = r.facets.map((f) => `“${f}”`).join(' or ');
  const own = `the vehicle's “${r.label}”`;
  const cmp = { minInclusive: ['at least', '≥', 'lower'], maxInclusive: ['at most', '≤', 'higher'] }[r.constraint];
  const steps = [{ topic: 'compare', text: cmp
    ? `Every ${names} value on the track must be ${cmp[0]} ${own} (sh:${r.constraint}: track value ${cmp[1]} vehicle value${r.unit ? `, in ${r.unit === 'M' ? 'm' : r.unit}` : ''}). The track is not compatible when one of its values is ${cmp[2]}.`
    : r.constraint === 'in'
      ? `Every value on the track must be one of the vehicle's (sh:in): it is compatible when each of its ${names} values is one of ${own}, and not compatible as soon as one is not.`
      : `The track must offer at least one of the vehicle's values (sh:hasValue): it is compatible when one of its ${names} values is one of ${own}, and not compatible when none is.` }];
  const src = r.none_from ? COMPAT_RULES.find((x) => x.key === r.none_from) : null;
  const reason = r.missing_reason ? `, with the reason “${r.missing_reason}”` : '';
  steps.push({ topic: 'missing', text: r.when_missing !== 'incompatible' ? `A track with no ${anyNames} value is unknown: the data cannot tell.`
    : src ? `A track with no ${anyNames} value is not compatible${reason}, but only when it has no “${src.label}” value either. Where it has one, a missing value is unknown.`
      : `A track with no ${anyNames} value is not compatible${reason}.` });
  if (r.none_value) steps.push({ topic: 'none', text: 'A vehicle that can also do without it (e.g. bi-mode) adds “Not electrified” to its values. It is then compatible with every track for this characteristic: where the track has no value, and also where its values do not match (the detail then says: Compatible in “Not electrified” mode). “Not electrified” itself is not compared with the track\'s values.' });
  if (src) steps.push({ topic: 'none', text: `When the vehicle's “${src.label}” includes “Not electrified”, it can do without it: every track is then compatible for this characteristic, whatever its values (the detail says: Compatible in “Not electrified” mode).` });
  if (r.without_value) steps.push({ topic: 'without', text: `A vehicle without the system picks “${r.without_label}”. It is then compatible with a track that has “N”, “0” or “NTC”, and not compatible with a track whose values are all others: that track requires the system.` });
  steps.push({ topic: 'rollup', text: 'Every running track of a section of line is checked. Per track the worst result of all characteristics counts (not compatible, then unknown, then compatible). The section of line takes the best track: green when at least one track is compatible on every characteristic, yellow when the best track is unknown or no track is linked to it, red when every track is not compatible.' });
  steps.push({ topic: 'empty', text: 'Leave it empty and it is not checked.' });
  return { vehicle_path: r.path.map(era).join(' / '), steps };
}
for (const r of COMPAT_RULES) {
  Object.assign(r, { section_facets: [], source: mockSource(r), explanation: mockExplain(r),
    infra: r.facets.map((fk) => ({ key: fk, label: fk, path: facetPath(fk), classes: ['running-track'], kind: r.kind === 'number' ? 'range' : 'terms', unit: r.unit })) });
}
const hash = (str) => { let x = 2166136261; for (const ch of str) x = Math.imul(x ^ ch.charCodeAt(0), 16777619) >>> 0; return x; };
const tracksBySol = new Map();
for (const sol of sols) {
  let st = hash(sol.id);
  const r = () => ((st = (Math.imul(st, 1664525) + 1013904223) >>> 0) / 4294967296);
  const tracks = [];
  for (let i = 0, n = r() < 0.6 ? 2 : 1; i < n; i++) {
    const el = sol.facets.electrified?.[0].value ?? true;
    const bel = sol.country === 'BEL';
    const f = { wheelSetGauge: [CV.g1435] };
    if (r() < 0.85) f.gaugingProfile = r() < 0.5 ? [CV.ga, CV.gb, CV.g1, CV.g2] : [CV.ga, CV.g1];
    if (el) {
      f['contactLineSystem.energySupplySystem'] = [r() < 0.8 ? (bel ? CV.dc3 : CV.dc15) : CV.ac25];
      f.tsiPantographHead = [CV.ep];
      if (bel) f.otherPantographHead = [CV.be];
    }
    const e = r();
    if (e < 0.35) f['etcs.etcsLevelType'] = [CV.l1]; else if (e < 0.6) f['etcs.etcsLevelType'] = [CV.l2];
    else if (e < 0.8) f['etcs.etcsLevelType'] = [e < 0.7 ? CV.ln : CV.l0];
    if (r() < 0.7) f['trainDetectionSystem.trainDetectionSystemType'] = r() < 0.5 ? [CV.tc] : [CV.tc, CV.wd];
    if (r() < 0.6) f['trackLoadCapability.loadCapabilityLineCategory'] = [r() < 0.7 ? CV.d4 : CV.c4];
    if (r() < 0.6) { const v = [400, 600, 740, 750][Math.floor(r() * 4)]; f.maxPermittedTrainLength = [{ value: v, label: String(v) }]; }
    tracks.push({ id: `${sol.id}_T${i + 1}`, iri: `${sol.iri.replace('/sectionsOfLine/', '/tracks/')}/${i + 1}`, label: `Track ${i + 1}`, class: 'running-track', facets: f });
  }
  tracksBySol.set(sol.id, tracks);
}
const compatRules = () => COMPAT_RULES.map((rule) => {
  const counts = new Map();
  for (const ts of tracksBySol.values()) for (const t of ts) for (const fk of rule.facets) for (const v of t.facets[fk] || []) {
    const c = counts.get(String(v.value)) || { value: v.value, label: v.label, count: 0 };
    c.count++; counts.set(String(v.value), c);
  }
  const vals = [...counts.values()].sort((a, b) => b.count - a.count);
  if (rule.none_value) {
    let n = 0;
    for (const ts of tracksBySol.values()) for (const t of ts) if (!rule.facets.some((fk) => (t.facets[fk] || []).length)) n++;
    vals.unshift({ value: rule.none_value, label: 'Not electrified', count: n, none: true });
  }
  if (rule.without_value) {
    const n = vals.filter((v) => rule.without_matches.includes(v.value)).reduce((s, v) => s + v.count, 0);
    vals.splice(0, vals.length, { value: rule.without_value, label: rule.without_label, count: n, without: true },
      ...vals.filter((v) => v.value !== rule.without_value));
  }
  if (rule.kind !== 'number') return { ...rule, values: vals };
  const nums = rule.term_values ? vals.map((v) => rule.term_values[v.value]).filter((x) => x != null) : vals.map((v) => v.value);
  return { ...rule, range: { min: nums.length ? Math.min(...nums) : null, max: nums.length ? Math.max(...nums) : null, count: vals.reduce((n, v) => n + v.count, 0) } };
});
const compatChecks = (profile) => {
  const empty = (v) => v == null || v === '' || (Array.isArray(v) && v.length === 0);
  return COMPAT_RULES.filter((r) => !empty(profile[r.key]));
};
// 0 = compatible, 1 = unknown, 2 = not compatible (as the real API)
const trackVals = (rule, track) => rule.facets.flatMap((fk) => (track.facets[fk] || []).map((x) => x.value));
// What a track without a value gives, and whether it is because the system is absent.
const missingOf = (rule, track, profile) => {
  const src = rule.none_from ? COMPAT_RULES.find((r) => r.key === rule.none_from) : rule;
  if (rule.when_missing !== 'incompatible' || (rule.none_from && trackVals(src, track).length)) return { code: 1 };
  const pv = profile[src.key];
  const ok = src.none_value && (Array.isArray(pv) ? pv : [pv]).includes(src.none_value);
  return { code: ok ? 0 : 2, note: rule.missing_reason };
};
// The vehicle can do without the system (it has the none value, e.g. not electrified):
// every track is compatible for this rule; the comparison only explains why.
const bypassOf = (rule, profile) => {
  const src = rule.none_from ? COMPAT_RULES.find((r) => r.key === rule.none_from) : rule;
  const pv = profile[src.key];
  return src.none_value && (Array.isArray(pv) ? pv : [pv]).includes(src.none_value) ? src.none_value : null;
};
const checkTrack = (rule, track, v, profile) => (bypassOf(rule, profile) ? 0 : rawCheck(rule, track, v, profile));
const rawCheck = (rule, track, v, profile) => {
  const vals = trackVals(rule, track);
  if (!vals.length) return missingOf(rule, track, profile).code;
  if (rule.term_values) {
    // a category must satisfy every filled rule with term_values on the same facet
    const sib = COMPAT_RULES.filter((r) => r.term_values && r.facets.join() === rule.facets.join() && profile[r.key] != null && profile[r.key] !== '');
    const fits = (t) => sib.every((r) => r.term_values[t] >= Number(Array.isArray(profile[r.key]) ? profile[r.key][0] : profile[r.key]));
    if (vals.some((t) => t in rule.term_values && fits(t))) return 0;
    return vals.some((t) => t in rule.term_values) ? 2 : 1;
  }
  if (rule.kind === 'number') {
    const n = Number(Array.isArray(v) ? v[0] : v);
    const bad = { minInclusive: (x) => x < n, maxInclusive: (x) => x > n, minExclusive: (x) => x <= n, maxExclusive: (x) => x >= n }[rule.constraint];
    return vals.some(bad) ? 2 : 0;
  }
  let want = (Array.isArray(v) ? v : [v]).map(String).filter((x) => x !== rule.none_value);
  if (rule.without_value && want.includes(rule.without_value)) want = [...want.filter((x) => x !== rule.without_value), ...rule.without_matches];
  if (rule.constraint === 'in') return vals.every((x) => want.includes(String(x))) ? 0 : 2;
  return vals.some((x) => want.includes(String(x))) ? 0 : 2;
};
const COLOURS = ['green', 'yellow', 'red'];
function noteOf(rule, t, v, profile) {
  if (!trackVals(rule, t).length) {
    const m = missingOf(rule, t, profile);
    if (m.note) return { note: m.note };
  }
  const by = bypassOf(rule, profile);
  return by && rawCheck(rule, t, v, profile) ? { note: 'Compatible in “Not electrified” mode', bypass: by } : {};
}
function compatOf(sol, checks, profile) {
  const tracks = (tracksBySol.get(sol.id) || []).map((t) => {
    const codes = checks.map((rule) => checkTrack(rule, t, profile[rule.key], profile));
    return { t, codes, worst: Math.max(0, ...codes) };
  });
  return { tracks, status: tracks.length ? Math.min(...tracks.map((x) => x.worst)) : 1 };
}

// ---------- line profile (same rules as api/app/profile.py, simplified) ----------
const LINK_KEYS = new Set(['opStart', 'opEnd']);
const DEFAULT_PARAMS = ['maximumPermittedSpeed', 'contactLineSystem.contactLineSystemType', 'contactLineSystem.energySupplySystem',
  'wheelSetGauge', 'etcs.etcsLevelType', 'gaugingProfile', 'lineCategory'];
const tracksOf = new Map();
for (const f of features) if (f.class === 'running-track' && f.parent) {
  if (!tracksOf.has(f.parent.iri)) tracksOf.set(f.parent.iri, []);
  tracksOf.get(f.parent.iri).push(f);
}
const solLen = (s) => s.facets.lengthOfSectionOfLine?.[0].value ?? +(dist(s.opStart, s.opEnd) * 1.08 / 1000).toFixed(3);
const stepStart = (st) => (st.rev ? st.sol.opEnd : st.sol.opStart).iri;
const stepEnd = (st) => (st.rev ? st.sol.opStart : st.sol.opEnd).iri;
const flip = (steps) => steps.slice().reverse().map((st) => ({ sol: st.sol, rev: !st.rev, gap: false }));

function adjacency(list) {
  const adj = new Map();
  const add = (n, e) => { if (!adj.has(n)) adj.set(n, []); adj.get(n).push(e); };
  for (const s of list) { add(s.opStart.iri, [s, s.opEnd.iri]); add(s.opEnd.iri, [s, s.opStart.iri]); }
  return adj;
}
function dijkstra(adj, src) {
  const d = new Map([[src, 0]]), prev = new Map(), done = new Set();
  for (;;) {
    let n = null;
    for (const [k, v] of d) if (!done.has(k) && (n === null || v < d.get(n))) n = k;
    if (n === null) break;
    done.add(n);
    for (const [s, m] of adj.get(n) || []) {
      const nd = d.get(n) + solLen(s);
      if (nd < (d.get(m) ?? Infinity)) { d.set(m, nd); prev.set(m, [n, s]); }
    }
  }
  return { d, prev };
}
function pathTo(prev, src, dst) {
  const out = [];
  for (let n = dst; n !== src;) { const [p, s] = prev.get(n); out.unshift({ sol: s, rev: s.opStart.iri !== p, gap: false }); n = p; }
  return out;
}
function longestRun(list) {
  const adj = adjacency(list);
  const leaves = [...adj.keys()].filter((n) => adj.get(n).length === 1).sort();
  const far = (r) => [...r.d.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0][0];
  const u = far(dijkstra(adj, leaves[0] ?? [...adj.keys()].sort()[0]));
  const r = dijkstra(adj, u);
  return pathTo(r.prev, u, far(r));
}
function orderSections(list) {
  const remaining = new Set(list);
  const runs = [];
  while (remaining.size) {
    // the connected group with the most kilometres first
    const groups = [];
    const seenS = new Set();
    for (const s of remaining) {
      if (seenS.has(s)) continue;
      const g = [], q = [s]; seenS.add(s);
      while (q.length) {
        const x = q.pop(); g.push(x);
        for (const y of remaining) if (!seenS.has(y) && [y.opStart, y.opEnd].some((o) => o === x.opStart || o === x.opEnd)) { seenS.add(y); q.push(y); }
      }
      groups.push(g);
    }
    groups.sort((a, b) => b.reduce((t, s) => t + solLen(s), 0) - a.reduce((t, s) => t + solLen(s), 0));
    const run = longestRun(groups[0]);
    runs.push(run);
    for (const st of run) remaining.delete(st.sol);
  }
  const opDist = (a, b) => (a === b ? 0 : dist(byIri.get(a), byIri.get(b)));
  runs.sort((a, b) => b.length - a.length);
  let chain = runs.shift() ?? [];
  while (runs.length) {
    let best = null;
    runs.forEach((r, i) => {
      for (const cand of [r, flip(r)]) {
        const tail = opDist(stepEnd(chain[chain.length - 1]), stepStart(cand[0]));
        const head = opDist(stepEnd(cand[cand.length - 1]), stepStart(chain[0]));
        if (!best || tail < best[0]) best = [tail, i, cand, true];
        if (head < best[0]) best = [head, i, cand, false];
      }
    });
    const [, i, cand, atTail] = best;
    runs.splice(i, 1);
    if (atTail) { cand[0].gap = stepStart(cand[0]) !== stepEnd(chain[chain.length - 1]); chain = chain.concat(cand); }
    else { chain[0].gap = stepEnd(cand[cand.length - 1]) !== stepStart(chain[0]); chain = cand.concat(chain); }
  }
  if (chain.length) chain[0].gap = false;
  return chain;
}
function reverseSteps(steps) {
  // gap flags stay between the same two sections
  return steps.slice().reverse().map((st, i) => ({ sol: st.sol, rev: !st.rev, gap: i > 0 ? steps[steps.length - i].gap : false }));
}
function summarize(kind, values) {
  if (kind === 'range') {
    const nums = values.map((v) => v.value).filter((v) => typeof v === 'number');
    return nums.length ? { min: Math.min(...nums), max: Math.max(...nums) } : {};
  }
  const m = new Map();
  for (const v of values) { const k = String(v.value); const c = m.get(k) || { value: v.value, label: v.label, n: 0 }; c.n++; m.set(k, c); }
  return { values: [...m.values()].sort((a, b) => b.n - a.n) };
}
function profile(req) {
  const solsAll = features.filter((f) => f.class === 'section-of-line');
  let steps, mode, line = null, missing = [];
  if (req.line) {
    const list = solsAll.filter((s) => s.facets['nationalLine.lineId']?.some((v) => v.value === req.line));
    const graph = list[0]?.graph ?? null;
    line = { id: req.line, graph_iri: graph, provider: graph ? (graph.endsWith('BEL') ? 'Infrabel' : 'ProRail') : null,
      alternatives: graph ? [{ graph_iri: graph, provider: graph.endsWith('BEL') ? 'Infrabel' : 'ProRail', sections: list.length }] : [] };
    steps = orderSections(list); mode = 'line';
  } else if (req.sections?.length) {
    const list = req.sections.map((i) => byIri.get(i)).filter((f) => f?.class === 'section-of-line');
    missing = req.sections.filter((i) => byIri.get(i)?.class !== 'section-of-line');
    steps = orderSections([...new Set(list)]); mode = 'sections';
  } else if (req.from && req.to) {
    const adj = adjacency(solsAll);
    const pts = [req.from, ...(req.via || []), req.to];
    steps = [];
    for (let i = 0; i + 1 < pts.length; i++) {
      const r = dijkstra(adj, pts[i]);
      if (!r.prev.has(pts[i + 1])) return [404, { detail: 'no connected sections of line between these operational points' }];
      steps = steps.concat(pathTo(r.prev, pts[i], pts[i + 1]));
    }
    mode = 'route';
  } else return [400, { detail: 'give a line, a list of sections, or from and to' }];
  if (mode !== 'route' && steps.filter((s) => s.rev).length * 2 > steps.length) steps = reverseSteps(steps);
  if (req.reverse) steps = reverseSteps(steps);
  const wanted = req.params ?? DEFAULT_PARAMS;
  const params = [...new Set(wanted)].map((k) => FACET_BY_KEY[k]).filter((f) => f && f.kind !== 'link')
    .map((f) => ({ key: f.key, label: f.label, kind: f.kind, unit: f.unit ?? null }));
  let km = 0, gaps = 0;
  const points = [], sections = [];
  const point = (iri, at) => { const o = byIri.get(iri); points.push({ iri, id: o.id, label: o.label, km: +at.toFixed(3), lon: o.geometry.coordinates[0], lat: o.geometry.coordinates[1] }); };
  steps.forEach((st, i) => {
    const s = st.sol, len = solLen(s);
    const gap = i > 0 && st.gap;
    if (gap) gaps++;
    if (i === 0 || gap) point(stepStart(st), km);
    const tracks = tracksOf.get(s.iri) || [];
    const cells = Object.fromEntries(params.map((p) => [p.key, summarize(p.kind, [...(s.facets[p.key] || []), ...tracks.flatMap((t) => t.facets[p.key] || [])])]));
    sections.push({
      id: s.id, iri: s.iri, label: s.label, graph_iri: s.graph, from: stepStart(st), to: stepEnd(st), reversed: st.rev,
      start_km: +km.toFixed(3), end_km: +(km + len).toFixed(3), length_km: len, length_estimated: !s.facets.lengthOfSectionOfLine,
      gap_before: gap, gap_km: gap ? +(dist(byIri.get(stepEnd(steps[i - 1])), byIri.get(stepStart(st))) / 1000).toFixed(3) : null,
      geometry: s.geometry, cells,
      tracks: tracks.map((t) => ({ iri: t.iri, label: t.label, direction: t.facets.trackDirection?.[0].label ?? null,
        values: Object.fromEntries(params.filter((p) => t.facets[p.key]).map((p) => [p.key, t.facets[p.key]])) })),
    });
    km += len;
    point(stepEnd(st), km);
  });
  return [200, { env: req.env, mode, line, params, length_km: +km.toFixed(3), gaps, sections, points, missing, truncated: false }];
}
function nationalLines(q) {
  const m = new Map();
  for (const s of features) for (const v of s.facets['nationalLine.lineId'] || []) {
    if (!norm(v.value).includes(norm(q))) continue;
    const k = `${v.value}|${s.graph}`;
    const c = m.get(k) || { line: v.value, graph_iri: s.graph, provider: s.graph.endsWith('BEL') ? 'Infrabel' : 'ProRail', sections: 0, length_km: 0 };
    c.sections++; c.length_km = +(c.length_km + solLen(s)).toFixed(3); m.set(k, c);
  }
  return [...m.values()].sort((a, b) => a.line.length - b.line.length || a.line.localeCompare(b.line));
}

// ---------- describe ----------
const uri = (value) => ({ type: 'uri', value });
const lit = (value, datatype, lang) => ({ type: 'literal', value: String(value), ...(datatype ? { datatype } : {}), ...(lang ? { lang } : {}) });
const RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#', RDFS = 'http://www.w3.org/2000/01/rdf-schema#';
const GSP = 'http://www.opengis.net/ont/geosparql#', XSD = 'http://www.w3.org/2001/XMLSchema#';
const SKOS = 'http://www.w3.org/2004/02/skos/core#', WGS = 'http://www.w3.org/2003/01/geo/wgs84_pos#';
const toWkt = (g) => g.type === 'GeometryCollection' ? `GEOMETRYCOLLECTION(${g.geometries.map(toWkt).join(', ')})`
  : g.type === 'Point' ? `POINT(${g.coordinates.join(' ')})`
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
      add(`${WGS}lat`, lit(repPoint(f.geometry)[1].toFixed(6), `${XSD}double`));
      add(`${WGS}long`, lit(repPoint(f.geometry)[0].toFixed(6), `${XSD}double`));
      add(`${ERA}opName`, lit(f.label, null, f.country === 'BEL' ? 'nl' : 'nl'));
    }
    if (f.class === 'section-of-line') {
      add(`${ERA}opStart`, uri(f.opStart.iri));
      add(`${ERA}opEnd`, uri(f.opEnd.iri));
      add(`${ERA}length`, lit(f.facets.length[0].value, `${XSD}double`));
      if (f.facets['max-speed']) add(`${ERA}maximumPermittedSpeed`, lit(f.facets['max-speed'][0].value, `${XSD}integer`));
      if (f.facets.electrified) add(`${ERA}hasElectrification`, lit(f.facets.electrified[0].value, `${XSD}boolean`));
    }
    if (f.class === 'running-track') add(`${ERA}isPartOf`, uri(f.sol.iri));
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

// ---------- OGC API - Features + TileJSON (docs/api.md, "GIS access") ----------
const OGC_CRS = ['http://www.opengis.net/def/crs/OGC/1.3/CRS84', 'http://www.opengis.net/def/crs/EPSG/0/4326', 'http://www.opengis.net/def/crs/EPSG/0/3857'];
const OGC_PARAMS = new Set(['limit', 'offset', 'bbox', 'bbox-crs', 'crs', 'datetime', 'f', 'q', 'dataset', 'values']);
const decodeState = (s) => JSON.parse(Buffer.from(s, 'base64url').toString());
const facetsOf = (cls) => FACETS.filter((f) => f.class === '*' || f.class === cls);
const bboxOf = (fs) => {
  const all = fs.flatMap((f) => coordsOf(f.geometry));
  return all.length ? [Math.min(...all.map((c) => c[0])), Math.min(...all.map((c) => c[1])), Math.max(...all.map((c) => c[0])), Math.max(...all.map((c) => c[1]))] : null;
};
const ogcError = (res, status, code, description) => send(res, status, { code, description });

/** Handles /api/ogc[/filtered/{state}]/..., /api/tiles.json and /api/style.json; false if not one of those. */
function gis(req, res, url) {
  const origin = `http://${req.headers.host || 'localhost'}`;
  const p = url.pathname;
  if (p === '/api/tiles.json' || p === '/api/style.json') {
    const enc = url.searchParams.get('state') || Buffer.from(JSON.stringify({ env: url.searchParams.get('env') || 'prod' })).toString('base64url');
    const st = decodeState(enc);
    const tiles = [`${origin}/api/tiles/{z}/{x}/{y}.pbf?state=${enc}`];
    const bounds = bboxOf(features.filter((f) => matches(f, { ...st, filters: {} }))) || [-180, -85, 180, 85];
    if (p === '/api/tiles.json') {
      send(res, 200, { tilejson: '3.0.0', name: 'RINF Atlas (mock)', tiles, minzoom: 0, maxzoom: 14, bounds,
        vector_layers: [{ id: 'features', fields: { id: 'Number', iri: 'String', class: 'String', label: 'String' } }] });
    } else {
      const layers = CLASSES.filter((c) => !st.classes?.length || st.classes.includes(c.class)).map((c) => (c.geometry === 'Point'
        ? { id: c.class, type: 'circle', source: 'atlas', 'source-layer': 'features', filter: ['==', 'class', c.class], paint: { 'circle-color': c.color } }
        : { id: c.class, type: 'line', source: 'atlas', 'source-layer': 'features', filter: ['==', 'class', c.class], paint: { 'line-color': c.color } }));
      send(res, 200, { version: 8, sources: { atlas: { type: 'vector', tiles, maxzoom: 14 } }, layers });
    }
    return true;
  }
  const m = /^\/api\/ogc(?:\/filtered\/([^/]+))?(\/.*)?$/.exec(p);
  if (!m) return false;
  let view = null;
  try { view = m[1] ? decodeState(m[1]) : null; } catch { ogcError(res, 400, 'InvalidParameterValue', 'bad state'); return true; }
  const rest = m[2] || '';
  const root = `${origin}/api/ogc${m[1] ? `/filtered/${m[1]}` : ''}`;
  const link = (href, rel, type = 'application/json') => ({ href, rel, type });
  const envs = view ? [view.env] : ENVS.map((e) => e.key);
  // One collection per class, or per geometry family (point/line/polygon) when a class mixes
  // them; the combined one then stays reachable but unlisted (like the real API).
  // GeometryCollections count in every family they have a part of (nested ones too).
  const famOf = (t) => (/Point/.test(t) ? 'point' : /LineString/.test(t) ? 'line' : 'polygon');
  const parts = (g) => (g.type === 'GeometryCollection' ? g.geometries.flatMap(parts) : [g]);
  const families = (f) => new Set(parts(f.geometry).map((g) => famOf(g.type)));
  const MULTI = { point: 'MultiPoint', line: 'MultiLineString', polygon: 'MultiPolygon' };
  const partOf = (g, fam) => {
    const ps = parts(g).filter((p) => famOf(p.type) === fam);
    // one type per layer: everything in a split family as its multi type
    return { type: MULTI[fam], coordinates: ps.flatMap((p) => (p.type.startsWith('Multi') ? p.coordinates : [p.coordinates])) };
  };
  const colls = envs.flatMap((env) => CLASSES.filter((c) => (!view?.classes?.length || view.classes.includes(c.class)) && features.some((f) => f.class === c.class))
    .flatMap((c) => {
      const fams = [...new Set(features.filter((f) => f.class === c.class).flatMap((f) => [...families(f)]))];
      const whole = { env, c, id: `${env}.${c.class}`, fam: null, listed: fams.length === 1 };
      return fams.length === 1 ? [whole] : [whole, ...fams.map((fam) => ({ env, c, id: `${env}.${c.class}.${fam}`, fam, listed: true }))];
    }));
  const inColl = (k, f) => f.class === k.c.class && (!k.fam || families(f).has(k.fam));
  const coll = (k) => ({
    id: k.id, title: `${k.c.label} (${[k.env.toUpperCase(), k.fam && `${k.fam}s`].filter(Boolean).join(', ')})`, itemType: 'feature',
    crs: OGC_CRS, storageCrs: OGC_CRS[0],
    description: `${k.c.label}. Facet query parameters: ${facetsOf(k.c.class).map((f) => f.key).join(', ')}`,
    extent: { spatial: { bbox: [bboxOf(features.filter((f) => inColl(k, f)))], crs: OGC_CRS[0] } },
    links: [link(`${root}/collections/${k.id}`, 'self'), link(`${root}/collections/${k.id}/items`, 'items', 'application/geo+json')],
  });
  if (rest === '' || rest === '/') {
    send(res, 200, { title: 'RINF Atlas (mock)', links: [link(root, 'self'), link(`${origin}/api/openapi.json`, 'service-desc', 'application/vnd.oai.openapi+json;version=3.1'),
      link(`${root}/conformance`, 'conformance'), link(`${root}/collections`, 'data')] });
    return true;
  }
  if (rest === '/conformance') {
    send(res, 200, { conformsTo: ['http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core', 'http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson', 'http://www.opengis.net/spec/ogcapi-features-2/1.0/conf/crs'] });
    return true;
  }
  if (rest === '/collections') { send(res, 200, { links: [link(`${root}/collections`, 'self')], collections: colls.filter((k) => k.listed).map(coll) }); return true; }
  const cm = /^\/collections\/([^/]+)(\/items(?:\/([^/]+))?)?$/.exec(rest);
  const hit = cm && colls.find((k) => k.id === cm[1]);
  if (!hit) { ogcError(res, 404, 'NotFound', 'no such collection'); return true; }
  if (!cm[2]) { send(res, 200, coll(hit)); return true; }
  const st = { ...(view || { env: hit.env, filters: {} }), classes: [hit.c.class], filters: { ...(view?.filters || {}) } };
  const toFeature = (f) => ({ type: 'Feature', id: f.id, geometry: hit.fam ? partOf(f.geometry, hit.fam) : f.geometry, properties: { iri: f.iri, label: f.label, class: hit.c.label,
    ...Object.fromEntries(facetsOf(hit.c.class).map((d) => { const vs = f.facets[d.key] || []; return [d.key, vs.length ? vs.map((v) => v.label ?? v.value).join('; ') : null]; })) } });
  if (cm[3]) {
    const f = features.find((x) => x.id === cm[3] && inColl(hit, x) && matches(x, st));
    if (!f) ogcError(res, 404, 'NotFound', 'no such feature'); else send(res, 200, toFeature(f));
    return true;
  }
  const q = url.searchParams;
  const keys = new Map(facetsOf(hit.c.class).map((f) => [f.key, f]));
  for (const k of new Set(q.keys())) {
    if (OGC_PARAMS.has(k)) continue;
    const f = keys.get(k);
    if (!f) { ogcError(res, 400, 'InvalidParameterValue', `unknown query parameter ${k}`); return true; }
    const raw = q.getAll(k).join(',');
    if (f.kind === 'range') { const [lo, hi] = raw.includes('..') ? raw.split('..') : [raw, raw]; st.filters[k] = { min: lo === '' ? null : Number(lo), max: hi === '' ? null : Number(hi) }; }
    else if (f.kind === 'boolean') st.filters[k] = raw.split(',').map((v) => v === 'true');
    else st.filters[k] = raw.split(',');
  }
  if (q.get('q')) st.q = q.get('q');
  if (q.get('bbox')) st.bbox = q.get('bbox').split(',').map(Number);
  const limit = Math.min(Number(q.get('limit') || 100), 10000), offset = Number(q.get('offset') || 0);
  const hits = features.filter((f) => inColl(hit, f) && matches(f, st));
  const page = hits.slice(offset, offset + limit);
  const pageUrl = (o) => { const u = new URL(url.href); u.searchParams.set('offset', String(o)); u.searchParams.set('limit', String(limit)); return `${origin}${u.pathname}${u.search}`; };
  const links = [link(`${origin}${url.pathname}${url.search}`, 'self', 'application/geo+json'), link(`${root}/collections/${cm[1]}`, 'collection')];
  if (offset + page.length < hits.length) links.push(link(pageUrl(offset + page.length), 'next', 'application/geo+json'));
  send(res, 200, { type: 'FeatureCollection', features: page.map(toFeature), numberMatched: hits.length, numberReturned: page.length,
    timeStamp: new Date().toISOString(), links });
  return true;
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
// ATLAS_DESCRIBE_URL=https://describe.example.org/describe?uri={iri}&env={env} shows the Full describe links.
const DESCRIBE_URL = process.env.ATLAS_DESCRIBE_URL || null;
const ENVS = [
  { key: 'prod', label: 'PROD', sparql: 'https://data-interop.era.europa.eu/api/sparql', describe_url: DESCRIBE_URL, default: true },
  { key: 'uat', label: 'UAT', sparql: 'https://uat.data-interop.era.europa.eu/api/sparql', describe_url: DESCRIBE_URL, default: false },
];
const checkEnv = (env) => ENVS.some((e) => e.key === env);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const MOCK_LAYERS = [
  { id: 'opentopomap', label: 'Topographic map', description: 'OpenTopoMap contours and relief over the base map', type: 'raster',
    tiles: ['a', 'b', 'c'].map((x) => `https://${x}.tile.opentopomap.org/{z}/{x}/{y}.png`), proxied: false,
    tile_size: 256, min_zoom: 0, max_zoom: 17, opacity: 0.6, position: 'below',
    attribution: '&copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)' },
];

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const p = url.pathname;
  const env = url.searchParams.get('env');
  try {
    if (req.method === 'OPTIONS') { res.writeHead(204, { 'access-control-allow-origin': '*', 'access-control-allow-headers': 'content-type' }); return res.end(); }
    if (p === '/api/envs') return send(res, 200, ENVS);
    // Deployer layers (config/layers.yaml): one public layer the browser loads directly.
    if (p === '/api/layers') return send(res, 200, MOCK_LAYERS);
    if (p === '/api/meta') {
      if (!checkEnv(env)) return send(res, 400, { detail: 'unknown env' });
      const classes = CLASSES.map((c, i) => ({ min_zoom: 0, draw_order: -i, ...c, count: features.filter((f) => f.class === c.class).length }));
      const qa = ['BEL', 'NLD'].map((c) => qaCounts(`${ERA}graph/rinf/${c}`));
      const quality = { enabled: true, validated: 2, violations: qa.reduce((a, q) => a + q.violations, 0), warnings: qa.reduce((a, q) => a + q.warnings, 0),
        infos: qa.reduce((a, q) => a + q.infos, 0), too_large: 1, checked_at: '2026-10-13T03:40:00Z', shapes_graph: SHAPES_SRC[env].graph,
        shapes_loaded_at: '2026-10-13T03:05:00Z', shapes: SHAPES_SRC[env], shapes_error: null, max_triples: MAX_TRIPLES,
        shacl_report_url: 'https://shacl-report.matdata.eu/' };
      // Views (shapes/views/*.ttl) need the worker's check results: the mock has none, so the view bar stays hidden.
      const check_results = { meets: 'https://rinf-atlas.matdata.eu/ns#Meets', no_data: 'https://rinf-atlas.matdata.eu/ns#NoData', fails: 'https://rinf-atlas.matdata.eu/ns#DoesNotMeet' };
      return send(res, 200, { classes, facets: FACETS, views: [], check_results, datasets: { total: 4, loaded: env === 'uat' ? 3 : 4, failed: env === 'uat' ? 1 : 0, pending: 0, last_ingest: '2026-10-13T03:12:00Z', last_check: '2026-10-14T09:30:00Z' }, quality });
    }
    if (req.method === 'POST' && p === '/api/theme') {
      const st = await readJson(req);
      if (!checkEnv(st.env)) return send(res, 400, { detail: 'unknown env' });
      await sleep(150);
      const legend = themeLegend(st, st.key);
      return legend ? send(res, 200, legend) : send(res, 400, { detail: `cannot colour by '${st.key}': not a terms, boolean or range facet` });
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
        const th = st.color ? theme(st.color) : null;
        if (st.color && !th) return send(res, 400, { detail: `cannot colour by '${st.color}'` });
        const items = hits.slice(offset, offset + limit).map((f) => {
          const [lon, lat] = repPoint(f.geometry);
          return { id: f.id, iri: f.iri, class: f.class, label: f.label, lon, lat, qa: qaLevel(f), ...(th ? { v: th.v.get(f) ?? null } : {}) };
        });
        return send(res, 200, { total: hits.length, unlocated: hits.filter((f) => !f.geometry).length, items });
      }
      if (!hits.length) return send(res, 200, { bbox: null });
      const all = hits.flatMap((f) => coordsOf(f.geometry));
      const xs = all.map((c) => c[0]), ys = all.map((c) => c[1]);
      return send(res, 200, { bbox: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] });
    }
    if (p === '/api/exports/profiles') {
      const columns = [
        { key: 'opName', label: 'Name of operational point', path: [`${ERA}opName`], rinf: '1.2.0.0.0.1' },
        { key: 'uopid', label: 'Unique OP ID', path: [`${ERA}uopid`], rinf: '1.2.0.0.0.2' },
        { key: 'netReference', label: 'Net reference', path: [`${ERA}netReference`], children: [
          { key: 'netReference.hasGeometry.asWKT', label: 'Net reference / Geometry / WKT', path: [`${ERA}netReference`, 'http://www.opengis.net/ont/geosparql#hasGeometry', 'http://www.opengis.net/ont/geosparql#asWKT'] }] },
      ];
      const classes = Object.fromEntries(CLASSES.map((c) => [c.class, { label: c.label, columns }]));
      return send(res, 200, { profiles: [{ key: 'full', label: 'Full RINF', comment: 'Every RINF parameter (mock).', classes }],
        formats: [['ttl', 'Turtle', 'rdf'], ['nt', 'N-Triples', 'rdf'], ['jsonld', 'JSON-LD', 'rdf'], ['csv', 'CSV', 'table'], ['xlsx', 'Excel', 'table'], ['geojson', 'GeoJSON', 'gis'], ['gpkg', 'GeoPackage', 'gis']]
          .map(([key, label, kind]) => ({ key, label, kind, ext: key, description: `${label} (mock)` })),
        limits: { max_entities: null, queue: 20 }, parts_predicate: `${ERA}hasPart` });
    }
    if (req.method === 'POST' && p === '/api/exports') {
      const body = await readJson(req);
      const hits = features.filter((f) => matches(f, body.state ?? {}));
      if (!hits.length) return send(res, 400, { detail: 'nothing matches this selection' });
      const id = `mock${exportJobs.size + 1}`;
      const job = { id, env: body.state.env, status: 'queued', format: body.format, profile: body.profile ?? null, shape: body.shape ?? 'wide',
        values: body.values ?? 'label', headers: body.headers ?? 'label', parts: !!body.parts, gzip: false, state: body.state,
        total: hits.length, done: 0, bytes: null, filename: null, warning: null, error: null, created_at: new Date().toISOString(),
        started_at: null, finished_at: null, expires_at: null, download: null, recipe: `/api/exports/${id}/recipe`, hits };
      exportJobs.set(id, job);
      // The mock "worker": a few progress ticks, then done.
      let step = 0;
      const tick = setInterval(() => {
        step += 1;
        job.status = 'running'; job.started_at ??= new Date().toISOString();
        job.done = Math.min(job.total, Math.round(job.total * step / 4));
        if (step >= 4) {
          clearInterval(tick);
          Object.assign(job, { status: 'done', finished_at: new Date().toISOString(), expires_at: new Date(Date.now() + 864e5).toISOString(),
            bytes: 1234 * job.total, filename: `rinf-atlas-mock.${job.format}`, download: `/api/exports/${id}/download` });
        }
      }, 700);
      return send(res, 202, { ...job, hits: undefined });
    }
    const em = p.match(/^\/api\/exports\/([^/]+)(?:\/(download|recipe))?$/);
    if (em) {
      const job = exportJobs.get(em[1]);
      if (!job) return send(res, 404, { detail: 'unknown export' });
      if (req.method === 'DELETE') { job.status = job.status === 'done' ? 'expired' : 'cancelled'; res.writeHead(204); return res.end(); }
      if (em[2] === 'recipe') { res.writeHead(200, { 'content-type': 'text/plain; charset=utf-8' }); return res.end(`RINF Atlas export recipe (mock)\nExport: ${job.id}\nSelection: ${JSON.stringify(job.state)}\n`); }
      if (em[2] === 'download') {
        if (job.status !== 'done') return send(res, 409, { detail: `export is ${job.status}` });
        const body = job.hits.map((f) => `<${f.iri}> <http://www.w3.org/2000/01/rdf-schema#label> ${JSON.stringify(f.label)} .`);
        res.writeHead(200, { 'content-type': 'text/plain; charset=utf-8', 'content-disposition': `attachment; filename="${job.filename}"` });
        return res.end(body.join('\n') + '\n');
      }
      return send(res, 200, { ...job, hits: undefined });
    }
    if (p === '/api/export') {
      // CSV only (the mock has no XLSX writer); good enough to exercise the download.
      const st = JSON.parse(Buffer.from(url.searchParams.get('state') || '', 'base64url').toString() || '{}');
      const q = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
      const rows = features.filter((f) => matches(f, st)).map((f) => [f.iri, f.label, f.class, ...repPoint(f.geometry)].map(q).join(','));
      res.writeHead(200, { 'content-type': 'text/csv; charset=utf-8', 'content-disposition': 'attachment; filename="rinf-atlas-mock.csv"' });
      return res.end(['IRI,Label,Class,Longitude,Latitude', ...rows].join('\r\n'));
    }
    if (gis(req, res, url)) return;
    if (/^\/api\/tiles\/\d+\/\d+\/\d+\.pbf$/.test(p)) { res.writeHead(204); return res.end(); }
    if (p === '/api/feature') {
      const id = url.searchParams.get('id');
      const f = id != null ? features.find((x) => String(x.id) === id) : byIri.get(url.searchParams.get('iri') || '');
      if (!f) return send(res, 404, { detail: 'feature not found' });
      const facetsOut = Object.fromEntries(Object.entries(f.facets).filter(([k]) => k !== 'quality').map(([k, vs]) => [k, vs.map(({ value, label }) => ({ value, label }))]));
      const facet_kinds = Object.fromEntries(Object.keys(facetsOut).map((k) => [k, LINK_KEYS.has(k) ? 'link' : FACET_BY_KEY[k]?.kind ?? 'terms']));
      const status = qaStatus(f.graph);
      const quality = { status, checked_at: status ? '2026-10-13T03:40:00Z' : null,
        note: status === 'skipped' ? 'not validated: too large (18,400,000 triples > limit 15,000,000)' : null,
        reason: status === 'skipped' ? 'too_large' : null, shapes: status ? SHAPES_SRC[env] : null,
        results: status === 'ok' ? (f.qaResults || []).map(({ sev, ...r }) => r) : [], truncated: false, report: status === 'ok', html: status === 'ok' };
      const source = f.parent ? 'from' : f.geometry ? 'path' : null;
      const position = { source, shape_file: shapeFiles().includes(`${f.class}.ttl`) ? `${f.class}.ttl` : null,
        rules: { geometry_path: ['((gsp:hasGeometry/gsp:asWKT)|(era:netReference/gsp:hasGeometry/gsp:asWKT))'],
          line_between: f.class === 'section-of-line' ? ['era:opStart', 'era:opEnd'] : null,
          geometry_from: f.class === 'operational-point' ? ['era:primaryLocation'] : f.class === 'section-of-line' ? [] : ['era:isPartOf|^era:hasPart'] },
        links: f.parent ? [{ rule: 'from', iri: f.parent.iri, label: f.parent.label, class: f.parent.class }] : [], more_links: false };
      return send(res, 200, { iri: f.iri, label: f.label, classes: [f.class], graph_iri: f.graph, facets: facetsOut, facet_kinds,
        geometry: f.geometry, geometry_source: source, position, quality });
    }
    if (p === '/api/shapes') {
      return send(res, 200, shapeFiles().map((name) => {
        const key = name.replace(/\.ttl$/, '');
        const c = CLASSES.find((x) => x.class === key);
        return { name, size: fs.statSync(new URL(name, SHAPES_DIR)).size, classes: c ? [{ class: c.class, label: c.label }] : [] };
      }));
    }
    if (p === '/api/shapes/file') {
      const name = url.searchParams.get('name') || '';
      if (!shapeFiles().includes(name)) return send(res, 404, { detail: 'no such shape file' });
      res.writeHead(200, { 'content-type': 'text/turtle; charset=utf-8',
        ...(url.searchParams.get('download') ? { 'content-disposition': `attachment; filename="${name}"` } : {}) });
      return res.end(fs.readFileSync(new URL(name, SHAPES_DIR), 'utf8'));
    }
    if (p === '/api/lines') {
      if (!checkEnv(env)) return send(res, 400, { detail: 'unknown env' });
      return send(res, 200, nationalLines(url.searchParams.get('q') || '').slice(0, Number(url.searchParams.get('limit') || 20)));
    }
    if (req.method === 'POST' && p === '/api/profile') {
      const body = await readJson(req);
      if (!checkEnv(body.env)) return send(res, 400, { detail: 'unknown env' });
      await sleep(120);
      const [status, out] = profile(body);
      return send(res, status, out);
    }
    if (p === '/api/describe') {
      const iri = url.searchParams.get('iri') || '';
      const dir = url.searchParams.get('direction') || 'out';
      await sleep(250); // it's "live" after all
      const triples = dir === 'in' ? describeIn(iri) : describeOut(iri);
      return send(res, 200, { iri, triples, labels: labelsFor(triples) });
    }
    if (p === '/api/compatibility/rules') {
      if (!checkEnv(env)) return send(res, 400, { detail: 'unknown env' });
      return send(res, 200, { track_classes: ['running-track'], section_class: 'section-of-line',
        class_labels: { 'running-track': 'Running track', 'section-of-line': 'Section of line' }, rules: compatRules() });
    }
    if (req.method === 'POST' && (p === '/api/compatibility' || p === '/api/compatibility/explain')) {
      const body = await readJson(req);
      if (!checkEnv(body.env)) return send(res, 400, { detail: 'unknown env' });
      const profile = body.profile || {};
      const checks = compatChecks(profile);
      if (!checks.length) return send(res, 400, { detail: 'the profile has no characteristic filled in that a rule checks' });
      const ignored = Object.keys(profile).filter((k) => !COMPAT_RULES.some((r) => r.key === k));
      await sleep(80);
      if (p === '/api/compatibility') {
        const out = { env: body.env, version: 'mock', checked: checks.map((c) => c.key), ignored, counts: {}, green: [], yellow: [], red: [] };
        for (const sol of sols) out[COLOURS[compatOf(sol, checks, profile).status]].push(sol.id);
        for (const c of COLOURS) out.counts[c] = out[c].length;
        return send(res, 200, out);
      }
      const sol = sols.find((x) => x.iri === body.iri || x.id === body.id);
      if (!sol) return send(res, 404, { detail: 'not an indexed section-of-line' });
      const r = compatOf(sol, checks, profile);
      return send(res, 200, {
        iri: sol.iri, id: sol.id, status: COLOURS[r.status], checked: checks.map((c) => c.key), ignored, section: [],
        tracks: r.tracks.map(({ t, codes, worst }) => ({
          id: t.id, iri: t.iri, label: t.label, class: t.class, status: COLOURS[worst],
          results: checks.map((rule, i) => ({ key: rule.key, result: ['ok', 'unknown', 'fail'][codes[i]],
            values: rule.facets.flatMap((fk) => (t.facets[fk] || []).map(({ value, label }) => ({ value, label }))),
            ...noteOf(rule, t, profile[rule.key], profile) })),
        })),
      });
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
