// Light / dark / auto (follow the OS) theme, remembered per browser.
export type ThemePref = 'auto' | 'light' | 'dark';

const KEY = 'atlas-theme';
const media = window.matchMedia('(prefers-color-scheme: dark)');
const listeners = new Set<(dark: boolean) => void>();

function readPref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === 'light' || v === 'dark' ? v : 'auto';
  } catch { return 'auto'; }
}

let pref: ThemePref = readPref();

export const isDark = () => (pref === 'auto' ? media.matches : pref === 'dark');
export const themePref = () => pref;

function apply() {
  const root = document.documentElement;
  if (pref === 'auto') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', pref);
  root.style.colorScheme = isDark() ? 'dark' : 'light';
  for (const l of listeners) l(isDark());
}

export function setTheme(p: ThemePref) {
  pref = p;
  try { if (p === 'auto') localStorage.removeItem(KEY); else localStorage.setItem(KEY, p); } catch { /* private mode */ }
  apply();
}

/** Called with the new state whenever the effective theme changes. */
export function onThemeChange(fn: (dark: boolean) => void) { listeners.add(fn); }
export function offThemeChange(fn: (dark: boolean) => void) { listeners.delete(fn); }

media.addEventListener('change', () => { if (pref === 'auto') apply(); });
apply();
