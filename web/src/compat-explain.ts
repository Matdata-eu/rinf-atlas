// "How is this checked?": a dialog explaining one compatibility rule. The text comes
// from the API (generated from the rule's annotations in shapes/compatibility.ttl), the
// facts from the rule's fields and the Turtle from its source, so any rule added to the
// shapes is explained without code here.
import type { CompatRule, CompatRules } from './api';
import { h, unitLabel } from './state';

const TOPICS: Record<string, string> = {
  compare: 'Comparison',
  missing: 'When there is no value',
  none: 'Vehicles that can do without it',
  fallback: 'Where the value comes from',
  rollup: 'Colour of the section of line',
  empty: 'Not filled in',
};

let dlg: HTMLDialogElement | null = null;
let opener: HTMLElement | null = null;

const code = (text: string) => h('code', {}, text);
const XSD = 'http://www.w3.org/2001/XMLSchema#';

function facts(r: CompatRule, rules: CompatRules | null): HTMLElement {
  const cls = (k: string) => rules?.class_labels?.[k] ?? k;
  const noneLabel = r.values?.find((v) => v.value === r.none_value)?.label;
  const row = (label: string, ...value: (Node | string | null)[]) => h('tr', {}, h('th', {}, label), h('td', {}, ...value));
  const rows = [
    row('Vehicle property', code(r.explanation?.vehicle_path || r.path.join(' / '))),
    ...(r.infra ?? []).map((f, i) => row(i ? '' : (r.applies_to === 'track' ? 'Track property' : 'Section property'),
      h('div', {}, f.label), f.path ? h('div', {}, code(f.path)) : null,
      f.classes.length ? h('div', { class: 'muted small' }, f.classes.map(cls).join(', ')) : null)),
    row('Constraint', code(`sh:${r.constraint}`)),
    r.unit ? row('Unit', unitLabel(r.unit)) : null,
    r.datatype ? row('Datatype', code(r.datatype.startsWith(XSD) ? `xsd:${r.datatype.slice(XSD.length)}` : r.datatype)) : null,
    row('Applies to', r.applies_to === 'track' ? 'Each track' : 'The section of line'),
    row('When missing', r.when_missing === 'incompatible' ? 'Not compatible' : 'Unknown'),
    r.none_value ? row('None value', noneLabel ?? r.none_value) : null,
    r.none_from ? row('None from', rules?.rules.find((x) => x.key === r.none_from)?.label ?? r.none_from) : null,
    r.missing_reason ? row('Missing reason', r.missing_reason) : null,
    r.section_facets?.length ? row('Section fallback', r.section_facets.join(', ')) : null,
  ];
  return h('table', { class: 'kv explain-facts' }, h('tbody', {}, ...rows));
}

export function openExplain(r: CompatRule, rules: CompatRules | null, from?: HTMLElement) {
  opener = from ?? null;
  if (!dlg) {
    dlg = h('dialog', { class: 'datasets-dialog about-dialog explain-dialog' });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg!.close(); });
    dlg.addEventListener('close', () => { opener?.focus(); opener = null; });
    document.body.append(dlg);
  }
  const title = `How is “${r.label}” checked?`;
  dlg.setAttribute('aria-label', title);
  const steps = (r.explanation?.steps ?? []).map((s) =>
    h('div', { class: `explain-step explain-${s.topic}` }, h('h3', {}, TOPICS[s.topic] ?? s.topic), h('p', {}, s.text)));
  dlg.replaceChildren(
    h('div', { class: 'dlg-head' },
      h('h2', {}, title),
      h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => dlg?.close() }, '×')),
    h('div', { class: 'about-body explain-body' },
      r.comment ? h('p', { class: 'muted' }, r.comment) : null,
      ...steps,
      h('h3', {}, 'Rule'),
      facts(r, rules),
      r.source
        ? h('details', { class: 'explain-source' },
            h('summary', {}, 'Rule in Turtle (shapes/compatibility.ttl)'),
            h('pre', {}, code(r.source)))
        : null));
  if (!dlg.open) dlg.showModal();
  dlg.querySelector<HTMLElement>('.dlg-head .icon-btn')?.focus();
}

/** The control that opens the explanation: a text link (profile editor) or a small "?" (detail table). */
export function explainButton(r: CompatRule, rules: CompatRules | null, style: 'link' | 'icon'): HTMLElement {
  const label = `How is “${r.label}” checked?`;
  const b: HTMLButtonElement = style === 'link'
    ? h('button', { type: 'button', class: 'explain-link small', 'aria-haspopup': 'dialog' }, 'How is this checked?')
    : h('button', { type: 'button', class: 'explain-icon', 'aria-haspopup': 'dialog', 'aria-label': label, title: label }, '?');
  b.addEventListener('click', (e) => { e.stopPropagation(); openExplain(r, rules, b); });
  return b;
}
