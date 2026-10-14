// MapLibre map: basemap, data layer (vector tiles or GeoJSON in mock mode), hover, selection.
import * as maplibregl from 'maplibre-gl';
import type { ExpressionSpecification, GeoJSONSource, MapGeoJSONFeature } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
// MapLibre 6 loads its worker relative to its own module URL, which bundling breaks.
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';

maplibregl.setWorkerUrl(workerUrl);
import { api, isAbort } from './api';
import { changed, debounce, queryState, store, type AppState } from './state';
import { isDark, onThemeChange } from './theme';

/** 'vector' = /api/tiles MVT (default); 'geojson' = points from POST /api/search (mock / no tile server). */
export const TILE_MODE: 'vector' | 'geojson' =
  new URLSearchParams(location.search).has('mock') || import.meta.env.VITE_TILE_MODE === 'geojson' ? 'geojson' : 'vector';

const SRC = 'rinf';
const SOURCE_LAYER = 'features';
const DATA_LAYERS = ['rinf-fill', 'rinf-line', 'rinf-circle'];
const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };

let map: maplibregl.Map;
let loaded = false;

const geomIs = (...types: string[]): ExpressionSpecification => ['in', ['geometry-type'], ['literal', types]];

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

/** Index of a class in the class list (lower = more important). */
function classRank(c: unknown): number {
  const i = (store.get().meta?.classes ?? []).findIndex((m) => m.class === c);
  return i < 0 ? 999 : i;
}

function sourceSpec(): maplibregl.SourceSpecification {
  if (TILE_MODE === 'geojson') return { type: 'geojson', data: EMPTY };
  return { type: 'vector', tiles: [api.tilesUrl(queryState(store.get(), false))], minzoom: 0, maxzoom: 14 };
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
    attributionControl: { compact: true },
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
          attribution: 'Style: <a href="https://www.openrailwaymap.org/">OpenRailwayMap</a> (CC-BY-SA)',
        },
        [SRC]: sourceSpec(),
        selection: { type: 'geojson', data: EMPTY },
      },
      layers: [
        { id: 'osm', type: 'raster', source: 'osm', paint: basemapPaint() },
        { id: 'orm', type: 'raster', source: 'orm', layout: { visibility: s.orm ? 'visible' : 'none' } },
        { id: 'rinf-fill', type: 'fill', source: SRC, ...sl, filter: geomIs('Polygon', 'MultiPolygon'),
          layout: { 'fill-sort-key': sortKeyExpr(s) },
          paint: { 'fill-color': colorExpr(s), 'fill-opacity': 0.35, 'fill-outline-color': colorExpr(s) } },
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
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
  // Raster basemap failures (offline, blocked hosts) are not fatal; keep the console clean.
  map.on('error', (e) => {
    const src = (e as { sourceId?: string }).sourceId;
    if (src === 'osm' || src === 'orm') return;
    console.warn('map:', e.error?.message ?? e);
  });
  onThemeChange((dark) => {
    for (const [k, v] of Object.entries(basemapPaint()) as [RasterPaintKey, number][]) map.setPaintProperty('osm', k, v);
    if (!dark) for (const k of ['raster-brightness-max', 'raster-contrast'] as const) map.setPaintProperty('osm', k, undefined);
  });

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
  map.on('load', () => { loaded = true; onMoveEnd(); refreshData(); });

  store.subscribe((s, prev) => {
    if (changed(s, prev, 'meta')) for (const id of DATA_LAYERS) {
      const prop = id === 'rinf-fill' ? (['fill-color', 'fill-outline-color'] as const) : id === 'rinf-line' ? (['line-color'] as const) : (['circle-color'] as const);
      for (const p of prop) map.setPaintProperty(id, p, colorExpr(s));
      map.setLayoutProperty(id, id === 'rinf-fill' ? 'fill-sort-key' : id === 'rinf-line' ? 'line-sort-key' : 'circle-sort-key', sortKeyExpr(s));
    }
    if (changed(s, prev, 'orm')) map.setLayoutProperty('orm', 'visibility', s.orm ? 'visible' : 'none');
    if (changed(s, prev, 'env', 'classes', 'filters', 'graphs', 'q')) refreshData();
  });
  return map;
}

let geojsonCtl: AbortController | null = null;

/** Point the data source at the current query state. */
function refreshData() {
  const s = store.get();
  if (!s.env || !loaded) return;
  const src = map.getSource(SRC);
  if (TILE_MODE === 'vector') {
    (src as maplibregl.VectorTileSource).setTiles([api.tilesUrl(queryState(s, false))]);
    return;
  }
  geojsonCtl?.abort();
  geojsonCtl = new AbortController();
  api.search(queryState(s, false), 5000, 0, geojsonCtl.signal).then((r) => {
    (src as GeoJSONSource).setData({
      type: 'FeatureCollection',
      features: r.items.map((it) => ({
        type: 'Feature', geometry: { type: 'Point', coordinates: [it.lon, it.lat] },
        properties: { id: it.id, iri: it.iri, class: it.class, label: it.label },
      })),
    });
  }).catch((e) => { if (!isAbort(e)) console.warn('geojson refresh failed', e); });
}

function wireInteraction(container: HTMLElement) {
  const tip = document.createElement('div');
  tip.className = 'map-tip';
  tip.hidden = true;
  container.append(tip);

  const pickFeature = (pt: maplibregl.PointLike): MapGeoJSONFeature | undefined => {
    const [x, y] = Array.isArray(pt) ? pt : [(pt as maplibregl.Point).x, (pt as maplibregl.Point).y];
    const pad = 5;
    const fs = map.queryRenderedFeatures([[x - pad, y - pad], [x + pad, y + pad]], { layers: DATA_LAYERS });
    // Prefer points over lines over polygons (points are drawn on top), then the class
    // drawn on top (earlier in the class list).
    const rank = (f: MapGeoJSONFeature) => DATA_LAYERS.length - DATA_LAYERS.indexOf(f.layer.id);
    return fs.sort((a, b) => rank(a) - rank(b) || classRank(a.properties.class) - classRank(b.properties.class))[0];
  };

  map.on('mousemove', (e) => {
    const f = pickFeature(e.point);
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
    tip.hidden = false;
    // Keep the tooltip inside the map: flip to the left/top near the edges.
    const x = e.point.x + 14 + tip.offsetWidth > container.clientWidth ? e.point.x - 14 - tip.offsetWidth : e.point.x + 14;
    const y = e.point.y + 14 + tip.offsetHeight > container.clientHeight ? e.point.y - 14 - tip.offsetHeight : e.point.y + 14;
    tip.style.transform = `translate(${x}px, ${y}px)`;
  });
  map.getCanvas().addEventListener('mouseleave', () => { tip.hidden = true; });

  map.on('click', (e) => {
    const f = pickFeature(e.point);
    if (!f) return;
    const detailTab = e.originalEvent.ctrlKey || e.originalEvent.metaKey ? 'describe' : 'properties';
    if (f.properties.iri) { store.set({ selected: String(f.properties.iri), detailTab }); return; }
    // Low-zoom tiles only carry id + class.
    const id = f.properties.id ?? f.id;
    if (id == null) return;
    api.featureById(store.get().env, id as number).then((ft) => store.set({ selected: ft.iri, detailTab }))
      .catch((err) => console.warn('feature lookup failed', err));
  });
}

export function setSelectionGeometry(g: GeoJSON.Geometry | null) {
  if (!map) return;
  const src = map.getSource('selection') as GeoJSONSource | undefined;
  src?.setData(g ? { type: 'Feature', geometry: g, properties: {} } : EMPTY);
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
