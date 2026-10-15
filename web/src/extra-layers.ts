// Extra map layers chosen by whoever deploys this instance (config/layers.yaml,
// docs/layers.md): one option each in the "Map layers" menu, off until ticked.
// Layers with a key come through the API's tile proxy, so the key stays there.
import type * as maplibregl from 'maplibre-gl';
import { api, type MapLayer } from './api';
import { onMapReady, OVERLAY_BEFORE } from './map';
import { changed, h, store } from './state';

/** Map ids of a layer's MapLibre source and style layers. */
const sourceId = (l: MapLayer) => `x-${l.id}`;
const layerIds = (l: MapLayer) => l.type === 'vector' ? (l.style ?? []).map((_, i) => `x-${l.id}-${i}`) : [`x-${l.id}`];

function addToMap(map: maplibregl.Map, l: MapLayer, on: boolean) {
  const tiles = l.proxied ? [api.layerTilesUrl(l.id)] : l.tiles;
  const common = { tiles, minzoom: l.min_zoom, maxzoom: l.max_zoom, attribution: l.attribution || undefined };
  map.addSource(sourceId(l), l.type === 'vector'
    ? { type: 'vector', ...common }
    : { type: 'raster', tileSize: l.tile_size, ...common });
  const before = l.position === 'above' ? OVERLAY_BEFORE : 'rinf-fill';
  const visibility = on ? 'visible' : 'none';
  if (l.type === 'vector') {
    (l.style ?? []).forEach((st, i) => {
      const spec = { ...st, id: layerIds(l)[i], source: sourceId(l), layout: { ...(st.layout as object | undefined), visibility } };
      try { map.addLayer(spec as maplibregl.LayerSpecification, before); } catch (e) { console.warn(`layer ${l.id}:`, e); }
    });
  } else {
    map.addLayer({ id: layerIds(l)[0], type: 'raster', source: sourceId(l), layout: { visibility },
      paint: { 'raster-opacity': l.opacity } }, before);
  }
}

export async function initExtraLayers(before: HTMLElement) {
  let list: MapLayer[];
  try { list = await api.layers(); } catch { return; } // an older API without /api/layers
  if (!list.length) return;
  const known = new Set(list.map((l) => l.id));
  const s0 = store.get();
  if (s0.layers.some((id) => !known.has(id))) store.set({ layers: s0.layers.filter((id) => known.has(id)) });

  const boxes = new Map<string, HTMLInputElement>();
  for (const l of list) {
    const box = h('input', { type: 'checkbox' });
    box.checked = store.get().layers.includes(l.id);
    box.addEventListener('change', () => {
      const cur = store.get().layers.filter((id) => id !== l.id);
      store.set({ layers: box.checked ? [...cur, l.id] : cur });
    });
    boxes.set(l.id, box);
    before.before(h('label', { class: 'layer-opt' }, box,
      h('span', {}, h('b', {}, l.label), l.description ? h('small', {}, l.description) : null)));
  }

  let map: maplibregl.Map | null = null;
  onMapReady((m) => {
    map = m;
    for (const l of list) addToMap(m, l, store.get().layers.includes(l.id));
  });
  store.subscribe((s, prev) => {
    if (!changed(s, prev, 'layers')) return;
    for (const l of list) {
      const on = s.layers.includes(l.id);
      boxes.get(l.id)!.checked = on;
      for (const id of layerIds(l)) if (map?.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
    }
  });
}
