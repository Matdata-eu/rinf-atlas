// Interactive RDF graph (vis-network), in the spirit of the MatGUI yasgui-graph-plugin:
// URI nodes light blue, literals grey boxes, edges labelled with the prefixed predicate;
// double-click a URI node to describe it and merge the result. Each expansion is a step
// in a history: back removes what it added, forward puts it back (Alt+Left / Alt+Right).
import { DataSet, Network, type Edge, type Node, type Options } from 'vis-network/standalone';
import type { DescribeResponse, RdfTerm } from './api';
import { prefixed, shortIri } from './rdf';
import { offThemeChange, onThemeChange } from './theme';

export interface GraphHandle {
  back(): void;
  forward(): void;
  destroy(): void;
}

/** What one expansion added, so it can be taken out (back) and put back (forward). */
interface Step { iri: string; nodes: GNode[]; edges: Edge[] }
type GNode = Node & { kind: 'uri' | 'literal' | 'bnode'; expanded?: boolean };

const URI_COLOR = { background: '#97C2FC', border: '#2B7CE9', highlight: { background: '#D2E5FF', border: '#2B7CE9' } };
const LIT_COLOR = { background: '#E5E5E5', border: '#A0A0A0', highlight: { background: '#F2F2F2', border: '#808080' } };
const LOADING_BORDER = '#FFA500';

const truncate = (s: string, n = 40) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

export function mountGraph(
  container: HTMLElement,
  rootIri: string,
  describe: (iri: string, dir?: 'out' | 'in') => Promise<DescribeResponse>,
  literalText: (t: RdfTerm) => string,
  onHistory: (canBack: boolean, canForward: boolean) => void = () => {},
): GraphHandle {
  const nodes = new DataSet<GNode>();
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

  const past: Step[] = [];
  let future: Step[] = [];
  let busy = false;
  const notify = () => onHistory(past.length > 0 && !busy, future.length > 0 && !busy);

  // Ids added by the merge in progress, recorded into its Step.
  let addedNodes: string[] = [];
  let addedEdges: string[] = [];
  const addNode = (n: GNode) => { nodes.add(n); addedNodes.push(String(n.id)); };

  const uriNode = (iri: string, labels: Record<string, string>) => {
    const label = labels[iri] ? `${truncate(labels[iri], 30)}\n${truncate(shortIri(iri), 36)}` : truncate(prefixed(iri), 40);
    const existing = nodes.get(iri);
    if (!existing) addNode({ id: iri, label, title: iri, shape: 'ellipse', color: URI_COLOR, kind: 'uri' });
    else if (labels[iri] && existing.label !== label) nodes.update({ id: iri, label });
  };

  const merge = (r: DescribeResponse) => {
    addedNodes = []; addedEdges = [];
    uriNode(r.iri, r.labels);
    for (const t of r.triples) {
      uriNode(t.s, r.labels);
      let target: string;
      if (t.o.type === 'uri') { uriNode(t.o.value, r.labels); target = t.o.value; }
      else {
        target = `${t.o.type}:${t.s}|${t.p}|${t.o.value}|${t.o.lang ?? ''}|${t.o.datatype ?? ''}`;
        if (!nodes.get(target)) {
          const text = t.o.type === 'bnode' ? `_:${t.o.value}` : literalText(t.o);
          addNode({ id: target, label: truncate(text, 48), title: text, shape: 'box', color: LIT_COLOR, kind: t.o.type });
        }
      }
      const eid = `${t.s}|${t.p}|${target}`;
      if (!edges.get(eid)) { edges.add({ id: eid, from: t.s, to: target, label: prefixed(t.p), title: t.p }); addedEdges.push(eid); }
    }
  };

  /** Selects a node and pans to it, so each history step shows where it happened. */
  const focus = (iri: string) => {
    if (!nodes.get(iri)) return;
    network.selectNodes([iri]);
    network.focus(iri, { scale: network.getScale(), animation: { duration: 300, easingFunction: 'easeInOutQuad' } });
  };

  const expand = async (iri: string, record = true) => {
    const n = nodes.get(iri);
    if (busy || !n || n.kind !== 'uri' || n.expanded) return;
    busy = true; notify();
    nodes.update({ id: iri, color: { ...URI_COLOR, border: LOADING_BORDER }, borderWidth: 3 });
    try {
      merge(await describe(iri, 'out'));
      nodes.update({ id: iri, expanded: true });
      if (record) {
        past.push({ iri, nodes: nodes.get(addedNodes), edges: edges.get(addedEdges) });
        future = []; // a new branch drops the old forward steps, as in a browser
      }
    } catch (e) {
      console.warn('describe failed', e);
    } finally {
      if (nodes.get(iri)) nodes.update({ id: iri, color: URI_COLOR, borderWidth: 1 });
      busy = false; notify();
    }
  };

  const back = () => {
    if (busy) return;
    const step = past.pop();
    if (!step) return;
    edges.remove(step.edges.map((e) => e.id!));
    nodes.remove(step.nodes.map((n) => n.id!));
    nodes.update({ id: step.iri, expanded: false });
    future.push(step);
    network.unselectAll();
    focus(past.length ? past[past.length - 1].iri : rootIri);
    notify();
  };

  const forward = () => {
    if (busy) return;
    const step = future.pop();
    if (!step) return;
    nodes.add(step.nodes);
    edges.add(step.edges);
    nodes.update({ id: step.iri, expanded: true });
    past.push(step);
    focus(step.iri);
    notify();
  };

  network.on('doubleClick', (p: { nodes: (string | number)[] }) => { if (p.nodes.length) expand(String(p.nodes[0])); });
  const onKey = (e: KeyboardEvent) => {
    if (!e.altKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
    e.preventDefault(); // keep the browser's own Alt+Left (page back) from leaving the app
    if (e.key === 'ArrowLeft') back(); else forward();
  };
  container.tabIndex = 0;
  container.addEventListener('keydown', onKey);

  uriNode(rootIri, {});
  expand(rootIri, false).then(() => {
    network.once('stabilizationIterationsDone', () => network.fit({ animation: false }));
    network.stabilize(150);
  });

  // Edge labels and lines follow the theme switch.
  const recolor = () => {
    const c = getComputedStyle(document.documentElement);
    network.setOptions({ edges: { font: { color: c.getPropertyValue('--text').trim() }, color: { color: c.getPropertyValue('--muted').trim() } } });
  };
  onThemeChange(recolor);
  return {
    back, forward,
    destroy: () => { offThemeChange(recolor); container.removeEventListener('keydown', onKey); network.destroy(); },
  };
}
