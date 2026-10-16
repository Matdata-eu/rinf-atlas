// MapLibre map: basemap, data layer (vector tiles or GeoJSON in mock mode), hover, selection.
import * as maplibregl from 'maplibre-gl';
import type { ExpressionSpecification, GeoJSONSource, MapGeoJSONFeature } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
// MapLibre 6 loads its worker relative to its own module URL, which bundling breaks.
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';

maplibregl.setWorkerUrl(workerUrl);
import { api, isAbort } from './api';
import { changed, debounce, queryState, store, type AppState, type MapView } from './state';
import { hasPrivacy, PRIVACY_URL } from './privacy';
import { isDark, onThemeChange } from './theme';
import { casingColor, describeValue, onThemeStyleChange, themeStyle } from './thematic';

/** 'vector' = /api/tiles MVT (default); 'geojson' = points from POST /api/search (mock / no tile server). */
export const TILE_MODE: 'vector' | 'geojson' =
  new URLSearchParams(location.search).has('mock') || import.meta.env.VITE_TILE_MODE === 'geojson' ? 'geojson' : 'vector';

const SRC = 'rinf';
const SOURCE_LAYER = 'features';
const DATA_LAYERS = ['rinf-fill', 'rinf-line', 'rinf-circle'];
const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };
const PROFILE_COLOR = '#d6336c';

let map: maplibregl.Map;
let loaded = false;
const loadWaiters: ((m: maplibregl.Map) => void)[] = [];

/** Runs fn with the map once its style has loaded (overlays such as the compatibility layer). */
export function onMapReady(fn: (m: maplibregl.Map) => void) {
  if (loaded) fn(map); else loadWaiters.push(fn);
}

/** Extra clickable line layers (overlays); picked like the data lines. */
const overlayLayers: string[] = [];
export function addPickableLayer(id: string) { if (!overlayLayers.includes(id)) overlayLayers.push(id); }

/** Optional extra line in the hover tooltip for a feature (e.g. its compatibility status). */
let tipNote: ((f: MapGeoJSONFeature) => string | null) | null = null;
export function setTipNote(fn: typeof tipNote) { tipNote = fn; }

/** Layer id the overlays are inserted below (the selection highlight stays on top). */
export const OVERLAY_BEFORE = 'sel-fill';

const geomIs = (...types: string[]): ExpressionSpecification => ['in', ['geometry-type'], ['literal', types]];

/** Data quality highlight: worst SHACL severity of the feature (tile property `qa`). */
export const QA_COLORS = { 3: '#d92d20', 2: '#f79009', 1: '#2e90fa' } as const;
const QA_COLOR = ['match', ['to-number', ['coalesce', ['get', 'qa'], 0]],
  3, QA_COLORS[3], 2, QA_COLORS[2], 1, QA_COLORS[1], 'rgba(0,0,0,0)'] as unknown as ExpressionSpecification;
const hasIssue = (...types: string[]): ExpressionSpecification =>
  ['all', geomIs(...types), ['>=', ['to-number', ['coalesce', ['get', 'qa'], 0]], 1]] as unknown as ExpressionSpecification;
const QA_LAYERS = ['qa-line', 'qa-circle'];
const QA_SEVERITY: Record<number, 'violation' | 'warning' | 'info'> = { 3: 'violation', 2: 'warning', 1: 'info' };

function colorExpr(s: AppState): ExpressionSpecification | string {
  const cls = s.meta?.classes ?? [];
  if (!cls.length) return '#888';
  return ['match', ['get', 'class'], ...cls.flatMap((c) => [c.class, c.color]), '#888'] as unknown as ExpressionSpecification;
}

/** Draw order: classes earlier in the class list (operational points first) are drawn on top. */
function sortKeyExpr(s: AppState): ExpressionSpecification | number {
  const cls = s.meta?.classes ?? [];
  if (!cls.length) return 0;
  return ['match', ['get', 'class'], ...cls.flatMap((c, i) => [c.class, cls.length - i]), 0] as unknown as ExpressionSpecification;
}

const LINE_W = [4, 1.2, 10, 2.5, 15, 5];
const CIRCLE_R = [4, 2.5, 10, 4.5, 15, 7];
/** Zoom-interpolated size, multiplied per feature by `mul` (an expression) in colour mode. */
function sized(stops: number[], mul?: ExpressionSpecification, add = 0): ExpressionSpecification {
  const out: unknown[] = ['interpolate', ['linear'], ['zoom']];
  for (let i = 0; i < stops.length; i += 2) {
    const v = mul ? ['*', stops[i + 1], mul] : stops[i + 1];
    out.push(stops[i], add ? ['+', v, add] : v);
  }
  return out as ExpressionSpecification;
}

const GEOM_FILTER: Record<string, ExpressionSpecification> = {
  'rinf-fill': geomIs('Polygon', 'MultiPolygon'),
  'rinf-line': geomIs('LineString', 'MultiLineString'),
  'rinf-line-casing': geomIs('LineString', 'MultiLineString'),
  'rinf-circle': geomIs('Point', 'MultiPoint'),
};

/** Paint, layout and filters of the data layers: class colours, or the "Colour by" theme. */
function applyStyle(s: AppState = store.get()) {
  if (!map || !map.getLayer('rinf-line')) return;
  const cls = colorExpr(s);
  const t = themeStyle(cls);
  const sort = sortKeyExpr(s);
  const sortKey = t ? (['+', sort, t.boost] as ExpressionSpecification) : sort;
  const op = t?.opacity ?? 1;
  map.setPaintProperty('rinf-fill', 'fill-color', t?.color ?? cls);
  map.setPaintProperty('rinf-fill', 'fill-outline-color', t?.color ?? cls);
  map.setPaintProperty('rinf-fill', 'fill-opacity', t ? ['*', 0.45, t.opacity] : 0.35);
  map.setPaintProperty('rinf-line', 'line-color', t?.color ?? cls);
  map.setPaintProperty('rinf-line', 'line-opacity', op);
  map.setPaintProperty('rinf-line', 'line-width', sized(LINE_W, t?.width));
  map.setPaintProperty('rinf-circle', 'circle-color', t?.color ?? cls);
  map.setPaintProperty('rinf-circle', 'circle-opacity', op);
  map.setPaintProperty('rinf-circle', 'circle-stroke-opacity', op);
  map.setPaintProperty('rinf-circle', 'circle-radius', sized(CIRCLE_R, t?.width));
  // A dark casing under coloured lines keeps light colours readable on the light map and
  // separates neighbouring lines on the dark one.
  map.setLayoutProperty('rinf-line-casing', 'visibility', t ? 'visible' : 'none');
  if (t) {
    map.setPaintProperty('rinf-line-casing', 'line-color', casingColor());
    map.setPaintProperty('rinf-line-casing', 'line-width', sized(LINE_W, t.width, 2));
    map.setPaintProperty('rinf-line-casing', 'line-opacity', ['case', ['has', 'v'], t.opacity, 0]);
  }
  for (const id of [...DATA_LAYERS, 'rinf-line-casing']) {
    map.setFilter(id, t ? ['all', GEOM_FILTER[id], t.visible] : GEOM_FILTER[id]);
    const sk = id === 'rinf-fill' ? 'fill-sort-key' : id === 'rinf-circle' ? 'circle-sort-key' : 'line-sort-key';
    map.setLayoutProperty(id, sk, sortKey);
  }
}

/** Index of a class in the class list (lower = more important). */
function classRank(c: unknown): number {
  const i = (store.get().meta?.classes ?? []).findIndex((m) => m.class === c);
  return i < 0 ? 999 : i;
}

/** The query state of the map tiles: the app's query plus the classes forced on at every zoom. */
function tileState(s: AppState = store.get()) {
  const st = queryState(s, false);
  return s.allZoom.length ? { ...st, all_zoom: s.allZoom } : st;
}

function sourceSpec(): maplibregl.SourceSpecification {
  if (TILE_MODE === 'geojson') return { type: 'geojson', data: EMPTY };
  return { type: 'vector', tiles: [api.tilesUrl(tileState(), store.get().colorBy)], minzoom: 0, maxzoom: 14 };
}

type RasterPaintKey = 'raster-brightness-max' | 'raster-saturation' | 'raster-contrast';

function basemapPaint(): Partial<Record<RasterPaintKey, number>> {
  return isDark()
    ? { 'raster-brightness-max': 0.72, 'raster-saturation': -0.5, 'raster-contrast': 0.1 }
    : { 'raster-saturation': -0.35 };
}

export function initMap(container: HTMLElement) {
  const s = store.get();
  const sl = TILE_MODE === 'vector' ? { 'source-layer': SOURCE_LAYER } : {};
  map = new maplibregl.Map({
    container,
    center: [s.view.lng, s.view.lat],
    zoom: s.view.zoom,
    attributionControl: false,
    style: {
      version: 8,
      sources: {
        osm: {
          type: 'raster', tileSize: 256, maxzoom: 19,
          tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        },
        orm: {
          type: 'raster', tileSize: 256, maxzoom: 19,
          tiles: ['a', 'b', 'c'].map((x) => `https://${x}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png`),
          attribution: 'Rail overlay: &copy; <a href="https://www.openrailwaymap.org/">OpenRailwayMap</a> (CC-BY-SA 2.0)',
        },
        [SRC]: sourceSpec(),
        selection: { type: 'geojson', data: EMPTY },
        profile: { type: 'geojson', data: EMPTY },
        'profile-ops': { type: 'geojson', data: EMPTY },
      },
      layers: [
        { id: 'osm', type: 'raster', source: 'osm', paint: basemapPaint() },
        { id: 'orm', type: 'raster', source: 'orm', layout: { visibility: s.orm ? 'visible' : 'none' } },
        { id: 'rinf-fill', type: 'fill', source: SRC, ...sl, filter: geomIs('Polygon', 'MultiPolygon'),
          layout: { 'fill-sort-key': sortKeyExpr(s) },
          paint: { 'fill-color': colorExpr(s), 'fill-opacity': 0.35, 'fill-outline-color': colorExpr(s) } },
        { id: 'rinf-line-casing', type: 'line', source: SRC, ...sl, filter: geomIs('LineString', 'MultiLineString'),
          layout: { 'line-cap': 'round', 'line-join': 'round', visibility: 'none' },
          paint: { 'line-color': '#000', 'line-width': 4 } },
        { id: 'qa-line', type: 'line', source: SRC, ...sl, filter: hasIssue('LineString', 'MultiLineString', 'Polygon', 'MultiPolygon'),
          layout: { 'line-cap': 'round', 'line-join': 'round', visibility: s.qa ? 'visible' : 'none' },
          paint: { 'line-color': QA_COLOR, 'line-opacity': 0.75, 'line-width': ['interpolate', ['linear'], ['zoom'], 4, 4, 10, 7, 15, 12] } },
        { id: 'rinf-line', type: 'line', source: SRC, ...sl, filter: geomIs('LineString', 'MultiLineString'),
          layout: { 'line-cap': 'round', 'line-join': 'round', 'line-sort-key': sortKeyExpr(s) },
          paint: { 'line-color': colorExpr(s), 'line-width': ['interpolate', ['linear'], ['zoom'], 4, 1.2, 10, 2.5, 15, 5] } },
        { id: 'rinf-circle', type: 'circle', source: SRC, ...sl, filter: geomIs('Point', 'MultiPoint'),
          layout: { 'circle-sort-key': sortKeyExpr(s) },
          paint: {
            'circle-color': colorExpr(s),
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 2.5, 10, 4.5, 15, 7],
            'circle-stroke-color': '#fff', 'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 4, 0.4, 10, 1.2],
          } },
        // Line profile: the sections in the strip, the hovered one, and their operational points.
        { id: 'profile-casing', type: 'line', source: 'profile', layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: { 'line-color': '#111', 'line-width': ['interpolate', ['linear'], ['zoom'], 4, 4, 12, 9], 'line-opacity': 0.45 } },
        { id: 'profile-line', type: 'line', source: 'profile', layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: { 'line-color': PROFILE_COLOR, 'line-width': ['interpolate', ['linear'], ['zoom'], 4, 2.5, 12, 5.5] } },
        { id: 'profile-hover', type: 'line', source: 'profile', filter: ['==', ['get', 'i'], -1],
          layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: { 'line-color': '#ffd400', 'line-width': ['interpolate', ['linear'], ['zoom'], 4, 5, 12, 10] } },
        { id: 'profile-ops', type: 'circle', source: 'profile-ops',
          paint: { 'circle-color': '#fff', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 2.5, 12, 5],
            'circle-stroke-color': PROFILE_COLOR, 'circle-stroke-width': 2 } },
        { id: 'qa-circle', type: 'circle', source: SRC, ...sl, filter: hasIssue('Point', 'MultiPoint'),
          layout: { visibility: s.qa ? 'visible' : 'none' },
          paint: {
            'circle-opacity': 0, 'circle-stroke-color': QA_COLOR,
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 5, 10, 8, 15, 11],
            'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 4, 1.5, 10, 2.5, 15, 3],
          } },
        { id: 'sel-fill', type: 'fill', source: 'selection', filter: geomIs('Polygon', 'MultiPolygon'),
          paint: { 'fill-color': '#ffd400', 'fill-opacity': 0.45 } },
        { id: 'sel-line-casing', type: 'line', source: 'selection', filter: geomIs('LineString', 'MultiLineString', 'Polygon', 'MultiPolygon'),
          layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#111', 'line-width': 9, 'line-opacity': 0.55 } },
        { id: 'sel-line', type: 'line', source: 'selection', filter: geomIs('LineString', 'MultiLineString', 'Polygon', 'MultiPolygon'),
          layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#ffd400', 'line-width': 5 } },
        { id: 'sel-circle', type: 'circle', source: 'selection', filter: geomIs('Point', 'MultiPoint'),
          paint: { 'circle-color': '#ffd400', 'circle-radius': 9, 'circle-stroke-color': '#111', 'circle-stroke-width': 2.5 } },
      ],
    },
  });
  if (import.meta.env.DEV) (window as unknown as { __map: maplibregl.Map }).__map = map; // for debugging / e2e
  // Zoom buttons (with "zoom to results") and the layers menu live in index.html; stack them top right.
  for (const id of ['map-ctrl-nav', 'map-ctrl-layers']) {
    const el = document.getElementById(id);
    if (el) map.addControl({ onAdd: () => el, onRemove: () => el.remove() }, 'top-right');
  }
  const zoomIn = document.getElementById('zoom-in') as HTMLButtonElement | null;
  const zoomOut = document.getElementById('zoom-out') as HTMLButtonElement | null;
  zoomIn?.addEventListener('click', (e) => map.zoomIn({}, { originalEvent: e }));
  zoomOut?.addEventListener('click', (e) => map.zoomOut({}, { originalEvent: e }));
  const zoomLimits = () => {
    const z = map.getZoom();
    if (zoomIn) zoomIn.disabled = z >= map.getMaxZoom();
    if (zoomOut) zoomOut.disabled = z <= map.getMinZoom();
  };
  map.on('zoom', zoomLimits);
  zoomLimits();
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
  // The current zoom, by the scale: classes appear from their own zoom (see the class list).
  const zoomLevel = document.createElement('div');
  zoomLevel.className = 'maplibregl-ctrl zoom-level';
  zoomLevel.title = 'Map zoom level (the first number after map= in the link)';
  const showZoom = () => { zoomLevel.textContent = `Zoom ${map.getZoom().toFixed(1)}`; };
  map.on('zoom', showZoom);
  showZoom();
  map.addControl({ onAdd: () => zoomLevel, onRemove: () => zoomLevel.remove() }, 'bottom-left');
  // Raster basemap failures (offline, blocked hosts) are not fatal; keep the console clean.
  map.on('error', (e) => {
    const src = (e as { sourceId?: string }).sourceId;
    if (src === 'osm' || src === 'orm' || src?.startsWith('x-')) return; // x-: deployer layers (extra-layers.ts)
    console.warn('map:', e.error?.message ?? e);
  });
  onThemeChange((dark) => {
    for (const [k, v] of Object.entries(basemapPaint()) as [RasterPaintKey, number][]) map.setPaintProperty('osm', k, v);
    if (!dark) for (const k of ['raster-brightness-max', 'raster-contrast'] as const) map.setPaintProperty('osm', k, undefined);
  });
  // Theme colours, hidden legend entries and the highlighted entry (also called on theme switch).
  onThemeStyleChange(() => applyStyle());

  wireInteraction(container);

  const onMoveEnd = debounce(() => {
    const c = map.getCenter();
    const b = map.getBounds();
    store.set({
      view: { lng: c.lng, lat: c.lat, zoom: map.getZoom() },
      bbox: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => +v.toFixed(5)) as AppState['bbox'],
    });
  }, 300);
  map.on('moveend', onMoveEnd);
  // Added once we know whether this instance has a privacy statement to link.
  hasPrivacy.then((ok) => map.addControl(new maplibregl.AttributionControl({
    compact: true,
    customAttribution: ok ? `<a href="${PRIVACY_URL}" target="_blank" rel="noopener">Privacy</a>` : undefined,
  })));
  map.on('load', () => {
    loaded = true; applyStyle(); onMoveEnd(); refreshData();
    for (const fn of loadWaiters.splice(0)) fn(map);
  });

  store.subscribe((s, prev) => {
    if (changed(s, prev, 'meta', 'colorBy')) applyStyle(s); // theme / hidden entries: onThemeStyleChange
    if (changed(s, prev, 'orm')) map.setLayoutProperty('orm', 'visibility', s.orm ? 'visible' : 'none');
    if (changed(s, prev, 'qa')) for (const id of QA_LAYERS) map.setLayoutProperty(id, 'visibility', s.qa ? 'visible' : 'none');
    if (changed(s, prev, 'env', 'classes', 'filters', 'graphs', 'q', 'colorBy', 'allZoom')) refreshData();
  });
  return map;
}

let geojsonCtl: AbortController | null = null;

/** Point the data source at the current query state. */
function refreshData() {
  const s = store.get();
  if (!s.env) return;
  const src = map.getSource(SRC);
  if (TILE_MODE === 'vector') {
    // As soon as the source exists (before 'load'), so no tiles are fetched without an environment.
    (src as maplibregl.VectorTileSource | undefined)?.setTiles([api.tilesUrl(tileState(s), s.colorBy)]);
    return;
  }
  if (!loaded) return;
  geojsonCtl?.abort();
  geojsonCtl = new AbortController();
  api.search(queryState(s, false), 5000, 0, geojsonCtl.signal, s.colorBy).then((r) => {
    (src as GeoJSONSource).setData({
      type: 'FeatureCollection',
      features: r.items.filter((it) => it.lon != null && it.lat != null).map((it) => ({
        type: 'Feature', geometry: { type: 'Point', coordinates: [it.lon!, it.lat!] },
        properties: { id: it.id, iri: it.iri, class: it.class, label: it.label, qa: it.qa ?? null, ...(it.v != null ? { v: it.v } : {}) },
      })),
    });
  }).catch((e) => { if (!isAbort(e)) console.warn('geojson refresh failed', e); });
}

function wireInteraction(container: HTMLElement) {
  const tip = document.createElement('div');
  tip.className = 'map-tip';
  tip.hidden = true;
  container.append(tip);

  /** Distinct features under the pointer, the one drawn on top first. */
  const pickFeatures = (pt: maplibregl.PointLike): MapGeoJSONFeature[] => {
    const [x, y] = Array.isArray(pt) ? pt : [(pt as maplibregl.Point).x, (pt as maplibregl.Point).y];
    const pad = 5;
    const layers = [...DATA_LAYERS, ...overlayLayers.filter((id) => map.getLayer(id) && map.getLayoutProperty(id, 'visibility') !== 'none')];
    const fs = map.queryRenderedFeatures([[x - pad, y - pad], [x + pad, y + pad]], { layers });
    // Prefer points over lines over polygons (points are drawn on top), then the class
    // drawn on top (earlier in the class list). Overlay lines rank like data lines. The line
    // profile can ask for one class first (sections of line while picking sections).
    const rank = (f: MapGeoJSONFeature) => (preferClass && f.properties.class === preferClass ? -100 : 0)
      + DATA_LAYERS.length - DATA_LAYERS.indexOf(overlayLayers.includes(f.layer.id) ? 'rinf-line' : f.layer.id);
    fs.sort((a, b) => rank(a) - rank(b) || classRank(a.properties.class) - classRank(b.properties.class));
    // A feature comes back once per tile (and per layer) it is drawn in.
    const seen = new Set<string>();
    return fs.filter((f) => {
      const k = String(f.properties.iri ?? `${f.properties.class}#${f.properties.id ?? f.id}`);
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  };
  /** Features a click can choose between: those drawn like the top one (point, line or area)
   *  with a label. Tracks share their section of line's geometry, so without a choice
   *  only the top one could ever be opened. Low-zoom tiles carry no labels: no choice there. */
  const clickChoices = (fs: MapGeoJSONFeature[]): MapGeoJSONFeature[] => {
    if (fs.length < 2 || clickHook) return fs.slice(0, 1);
    const kind = (f: MapGeoJSONFeature) => overlayLayers.includes(f.layer.id) ? 'rinf-line' : f.layer.id;
    const same = fs.filter((f) => kind(f) === kind(fs[0]));
    if (!same.every((f) => f.properties.label)) return fs.slice(0, 1);
    return same.sort((a, b) => classRank(a.properties.class) - classRank(b.properties.class)
      || String(a.properties.label).localeCompare(String(b.properties.label), undefined, { numeric: true }));
  };

  const picker = document.createElement('div');
  picker.className = 'map-picker';
  picker.setAttribute('role', 'menu');
  picker.hidden = true;
  container.append(picker);
  const closePicker = () => { if (!picker.hidden) { picker.hidden = true; picker.replaceChildren(); } };
  picker.addEventListener('keydown', (e) => {
    const items = [...picker.querySelectorAll<HTMLButtonElement>('button')];
    const i = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === 'Escape') { e.stopPropagation(); closePicker(); map.getCanvas().focus(); }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus();
    }
  });
  document.addEventListener('pointerdown', (e) => { if (!picker.contains(e.target as Node)) closePicker(); }, true);
  map.on('movestart', closePicker);

  const classMeta = (f: MapGeoJSONFeature) => store.get().meta?.classes.find((c) => c.class === f.properties.class);

  const openFeature = (f: MapGeoJSONFeature, detailTab: 'describe' | 'properties') => {
    if (clickHook?.({ iri: f.properties.iri as string | undefined, id: (f.properties.id ?? f.id) as string | number | undefined,
      class: String(f.properties.class ?? '') })) return;
    if (f.properties.iri) { store.set({ selected: String(f.properties.iri), detailTab }); return; }
    // Low-zoom tiles only carry id + class.
    const id = f.properties.id ?? f.id;
    if (id == null) return;
    api.featureById(store.get().env, id as number).then((ft) => store.set({ selected: ft.iri, detailTab }))
      .catch((err) => console.warn('feature lookup failed', err));
  };

  const showPicker = (fs: MapGeoJSONFeature[], at: maplibregl.Point, detailTab: 'describe' | 'properties') => {
    picker.replaceChildren();
    const head = document.createElement('div');
    head.className = 'map-picker-head';
    // With the data quality layer on, mark the features that have issues, as the map does.
    const qaOn = store.get().qa && !!store.get().meta?.quality?.enabled;
    const sevOf = (f: MapGeoJSONFeature) => qaOn ? QA_SEVERITY[Number(f.properties.qa ?? 0)] : undefined;
    const issues = fs.filter((f) => sevOf(f)).length;
    head.textContent = `${fs.length} features here` + (qaOn ? `, ${issues || 'none'} with data issues` : '');
    picker.append(head);
    for (const f of fs) {
      const meta = classMeta(f);
      const b = document.createElement('button');
      b.type = 'button';
      b.setAttribute('role', 'menuitem');
      const sw = document.createElement('span');
      sw.className = 'swatch';
      sw.style.background = meta?.color ?? '#888';
      const name = document.createElement('span');
      name.className = 'map-picker-label';
      name.textContent = String(f.properties.label);
      const cls = document.createElement('small');
      cls.textContent = meta?.label ?? String(f.properties.class ?? '');
      b.append(sw, name, cls);
      const sev = sevOf(f);
      if (sev) {
        const dot = document.createElement('i');
        dot.className = `qa-dot sev-${sev}`;
        dot.title = `Data quality: worst issue is a ${sev}`;
        dot.setAttribute('role', 'img');
        dot.setAttribute('aria-label', `Data quality ${sev}`);
        b.append(dot);
      }
      b.addEventListener('click', (e) => {
        closePicker();
        openFeature(f, e.ctrlKey || e.metaKey ? 'describe' : detailTab);
      });
      picker.append(b);
    }
    tip.hidden = true;
    picker.hidden = false;
    const x = at.x + 4 + picker.offsetWidth > container.clientWidth ? Math.max(0, at.x - 4 - picker.offsetWidth) : at.x + 4;
    const y = at.y + 4 + picker.offsetHeight > container.clientHeight ? Math.max(0, at.y - 4 - picker.offsetHeight) : at.y + 4;
    picker.style.transform = `translate(${x}px, ${y}px)`;
    picker.querySelector('button')?.focus({ preventScroll: true });
  };

  map.on('mousemove', (e) => {
    if (!picker.hidden) return;
    const fs = pickFeatures(e.point);
    const f = fs[0];
    map.getCanvas().style.cursor = f ? 'pointer' : '';
    if (!f) { tip.hidden = true; return; }
    const meta = store.get().meta?.classes.find((c) => c.class === f.properties.class);
    tip.replaceChildren();
    const sw = document.createElement('span');
    sw.className = 'swatch';
    sw.style.background = meta?.color ?? '#888';
    const strong = document.createElement('strong');
    strong.textContent = String(f.properties.label ?? meta?.label ?? f.properties.class ?? '');
    const small = document.createElement('small');
    small.textContent = f.properties.label ? meta?.label ?? String(f.properties.class ?? '') : 'Click for details';
    tip.append(strong, document.createElement('br'), sw, small);
    const val = describeValue(f.properties);
    if (val) {
      const vs = document.createElement('span');
      vs.className = 'swatch';
      vs.style.background = val[0];
      const vt = document.createElement('small');
      vt.textContent = val[1];
      tip.append(document.createElement('br'), vs, vt);
    }
    const more = clickChoices(fs).length - 1;
    if (more > 0) {
      const m = document.createElement('small');
      m.className = 'tip-note';
      m.textContent = `+${more} more here, click to choose`;
      tip.append(document.createElement('br'), m);
    }
    const note = tipNote?.(f);
    if (note) {
      const n = document.createElement('small');
      n.className = 'tip-note';
      n.textContent = note;
      tip.append(document.createElement('br'), n);
    }
    tip.hidden = false;
    // Keep the tooltip inside the map: flip to the left/top near the edges.
    const x = e.point.x + 14 + tip.offsetWidth > container.clientWidth ? e.point.x - 14 - tip.offsetWidth : e.point.x + 14;
    const y = e.point.y + 14 + tip.offsetHeight > container.clientHeight ? e.point.y - 14 - tip.offsetHeight : e.point.y + 14;
    tip.style.transform = `translate(${x}px, ${y}px)`;
  });
  map.getCanvas().addEventListener('mouseleave', () => { tip.hidden = true; });

  map.on('mousemove', 'profile-line', (e) => {
    const i = e.features?.[0]?.properties?.i;
    profileHoverCb?.(typeof i === 'number' ? i : null);
  });
  map.on('mouseleave', 'profile-line', () => profileHoverCb?.(null));

  map.on('click', (e) => {
    const fs = clickChoices(pickFeatures(e.point));
    if (!fs.length) return;
    const detailTab = e.originalEvent.ctrlKey || e.originalEvent.metaKey ? 'describe' : 'properties';
    if (fs.length > 1) showPicker(fs, e.point, detailTab);
    else openFeature(fs[0], detailTab);
  });
}

// ---------- line profile ----------
export interface ClickedFeature { iri?: string; id?: string | number; class: string }
let clickHook: ((f: ClickedFeature) => boolean) | null = null;
let preferClass: string | null = null;
let profileHoverCb: ((i: number | null) => void) | null = null;

/** While set, map clicks on data features go here first; returning true skips opening the detail panel.
 *  `prefer`: a class picked before any other under the pointer (hover and click). */
export function setClickHook(fn: ((f: ClickedFeature) => boolean) | null, prefer: string | null = null) {
  clickHook = fn;
  preferClass = fn ? prefer : null;
}
export function onProfileHover(fn: (i: number | null) => void) { profileHoverCb = fn; }

/** Sections (with property `i` = index in the strip) and operational points of the line profile. */
export function setProfileData(sections: GeoJSON.FeatureCollection, points: GeoJSON.FeatureCollection) {
  const set = () => {
    (map.getSource('profile') as GeoJSONSource).setData(sections);
    (map.getSource('profile-ops') as GeoJSONSource).setData(points);
  };
  if (loaded) set(); else map.once('load', set);
}

export function setProfileHover(i: number | null) {
  if (map?.getLayer('profile-hover')) map.setFilter('profile-hover', ['==', ['get', 'i'], i ?? -1]);
}

/** Pick up a container size change (panel opened or resized). */
export function resizeMap() { map?.resize(); }

export function setSelectionGeometry(g: GeoJSON.Geometry | null) {
  if (!map) return;
  const src = map.getSource('selection') as GeoJSONSource | undefined;
  src?.setData(g ? { type: 'Feature', geometry: g, properties: {} } : EMPTY);
}

/** Moves the map to a view from a link (Back / Forward), unless it is already there. */
export function jumpToView(v: MapView) {
  if (!map) return;
  const c = map.getCenter();
  if (Math.abs(c.lng - v.lng) < 1e-5 && Math.abs(c.lat - v.lat) < 1e-5 && Math.abs(map.getZoom() - v.zoom) < 0.01) return;
  map.jumpTo({ center: [v.lng, v.lat], zoom: v.zoom });
}

export function flyTo(lon: number, lat: number, minZoom = 12) {
  map.resize(); // pick up layout changes (e.g. detail panel just opened) before animating
  map.flyTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), minZoom), essential: true });
}

export function fitGeometry(g: GeoJSON.Geometry) {
  const coords: number[][] = [];
  const walk = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === 'number') coords.push(c as number[]);
    else if (Array.isArray(c)) c.forEach(walk);
  };
  walk('coordinates' in g ? g.coordinates : []);
  if (!coords.length) return;
  const xs = coords.map((c) => c[0]), ys = coords.map((c) => c[1]);
  fitBbox([Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)], 13);
}

export function fitBbox(b: [number, number, number, number], maxZoom = 14) {
  if (b[0] === b[2] && b[1] === b[3]) { flyTo(b[0], b[1], Math.min(maxZoom, 13)); return; }
  map.resize();
  map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, maxZoom, essential: true });
}
