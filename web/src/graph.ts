// Interactive RDF graph (vis-network), in the spirit of the MatGUI yasgui-graph-plugin:
// URI nodes light blue, literals grey boxes, edges labelled with the prefixed predicate;
// double-click a URI node to describe it and merge the result.
import { DataSet, Network, type Edge, type Node, type Options } from 'vis-network/standalone';
import type { DescribeResponse, RdfTerm } from './api';
import { prefixed, shortIri } from './rdf';
import { offThemeChange, onThemeChange } from './theme';

export interface GraphHandle { destroy(): void }

const URI_COLOR = { background: '#97C2FC', border: '#2B7CE9', highlight: { background: '#D2E5FF', border: '#2B7CE9' } };
const LIT_COLOR = { background: '#E5E5E5', border: '#A0A0A0', highlight: { background: '#F2F2F2', border: '#808080' } };
const LOADING_BORDER = '#FFA500';

const truncate = (s: string, n = 40) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

export function mountGraph(
  container: HTMLElement,
  rootIri: string,
  describe: (iri: string, dir?: 'out' | 'in') => Promise<DescribeResponse>,
  literalText: (t: RdfTerm) => string,
): GraphHandle {
  const nodes = new DataSet<Node & { kind: 'uri' | 'literal' | 'bnode'; expanded?: boolean }>();
  const edges = new DataSet<Edge>();
  const css = getComputedStyle(document.documentElement);
  const textColor = css.getPropertyValue('--text').trim() || '#222';
  const edgeColor = css.getPropertyValue('--muted').trim() || '#888';

  const options: Options = {
    nodes: { font: { size: 13, color: '#1a1a1a' }, borderWidth: 1, margin: { top: 6, right: 8, bottom: 6, left: 8 } },
    edges: {
      arrows: { to: { enabled: true, scaleFactor: 0.6 } },
      font: { size: 11, color: textColor, strokeWidth: 0, align: 'middle' },
      color: { color: edgeColor, highlight: '#2B7CE9' },
      smooth: { enabled: true, type: 'dynamic', roundness: 0.5 },
    },
    physics: { solver: 'forceAtlas2Based', forceAtlas2Based: { gravitationalConstant: -60, springLength: 120 }, stabilization: { iterations: 150 } },
    interaction: { hover: true, tooltipDelay: 150 },
  };
  const network = new Network(container, { nodes, edges }, options);

  const uriNode = (iri: string, labels: Record<string, string>) => {
    const label = labels[iri] ? `${truncate(labels[iri], 30)}\n${truncate(shortIri(iri), 36)}` : truncate(prefixed(iri), 40);
    const existing = nodes.get(iri);
    if (!existing) nodes.add({ id: iri, label, title: iri, shape: 'ellipse', color: URI_COLOR, kind: 'uri' });
    else if (labels[iri] && existing.label !== label) nodes.update({ id: iri, label });
  };

  const merge = (r: DescribeResponse) => {
    uriNode(r.iri, r.labels);
    for (const t of r.triples) {
      uriNode(t.s, r.labels);
      let target: string;
      if (t.o.type === 'uri') { uriNode(t.o.value, r.labels); target = t.o.value; }
      else {
        target = `${t.o.type}:${t.s}|${t.p}|${t.o.value}|${t.o.lang ?? ''}|${t.o.datatype ?? ''}`;
        if (!nodes.get(target)) {
          const text = t.o.type === 'bnode' ? `_:${t.o.value}` : literalText(t.o);
          nodes.add({ id: target, label: truncate(text, 48), title: text, shape: 'box', color: LIT_COLOR, kind: t.o.type });
        }
      }
      const eid = `${t.s}|${t.p}|${target}`;
      if (!edges.get(eid)) edges.add({ id: eid, from: t.s, to: target, label: prefixed(t.p), title: t.p });
    }
  };

  const expand = async (iri: string) => {
    const n = nodes.get(iri);
    if (!n || n.kind !== 'uri' || n.expanded) return;
    nodes.update({ id: iri, color: { ...URI_COLOR, border: LOADING_BORDER }, borderWidth: 3 });
    try {
      merge(await describe(iri, 'out'));
      nodes.update({ id: iri, expanded: true });
    } catch (e) {
      console.warn('describe failed', e);
    } finally {
      if (nodes.get(iri)) nodes.update({ id: iri, color: URI_COLOR, borderWidth: 1 });
    }
  };

  network.on('doubleClick', (p: { nodes: (string | number)[] }) => { if (p.nodes.length) expand(String(p.nodes[0])); });
  uriNode(rootIri, {});
  expand(rootIri).then(() => {
    network.once('stabilizationIterationsDone', () => network.fit({ animation: false }));
    network.stabilize(150);
  });

  // Edge labels and lines follow the theme switch.
  const recolor = () => {
    const c = getComputedStyle(document.documentElement);
    network.setOptions({ edges: { font: { color: c.getPropertyValue('--text').trim() }, color: { color: c.getPropertyValue('--muted').trim() } } });
  };
  onThemeChange(recolor);
  return { destroy: () => { offThemeChange(recolor); network.destroy(); } };
}
