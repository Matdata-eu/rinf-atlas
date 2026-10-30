// The open view (shapes/views/*.ttl) and the results of its requirement checks (shapes/views/*.ttl), stored by the worker as
// facet values: fixed colours wherever they are shown (map, legend, filters, scorecard).
import type { CheckResult, FacetMeta, TermValue, ViewMeta } from './api';
import { store, type AppState } from './state';

export const CHECK_COLOURS: Record<CheckResult, string> = { meets: '#12b76a', no_data: '#f5b301', fails: '#e5484d' };

/** Which check result a facet value is, if it is one. */
export function checkResult(value: TermValue | null | undefined): CheckResult | null {
  const r = store.get().meta?.check_results;
  if (!r || typeof value !== 'string') return null;
  return (Object.keys(r) as CheckResult[]).find((k) => r[k] === value) ?? null;
}

/** The fixed map colour of a check result value, or null for any other value. */
export function checkColour(value: TermValue | null | undefined): string | null {
  const r = checkResult(value);
  return r ? CHECK_COLOURS[r] : null;
}

export function viewMeta(s: AppState = store.get()): ViewMeta | null {
  return s.lens ? s.meta?.views?.find((v) => v.key === s.lens) ?? null : null;
}

/** Facet keys of the view: summary, checks, then parameters. */
export function viewKeys(v: ViewMeta): string[] {
  return [...(v.summary_key ? [v.summary_key] : []), ...v.checks.map((c) => c.key), ...v.params];
}

/** The view's parameters and checks as facets that apply to every class owning them (unscoped keys). */
export function viewFacets(s: AppState): FacetMeta[] {
  const v = viewMeta(s);
  if (!v) return [];
  const facets = s.meta?.facets ?? [];
  const checkLabel = new Map<string, string>(v.checks.map((c) => [c.key, c.label]));
  if (v.summary_key) checkLabel.set(v.summary_key, v.summary_label ?? v.summary_key);
  return viewKeys(v).flatMap((k, i) => {
    const f = facets.find((x) => x.key === k && (x.class === '*' || v.classes.includes(x.class))) ?? facets.find((x) => x.key === k);
    if (!f) return [];
    return [{ ...f, class: '*', label: checkLabel.get(k) ?? f.label, position: i, open: checkLabel.has(k) }];
  });
}

