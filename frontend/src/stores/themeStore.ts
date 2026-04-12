import { create } from 'zustand';

type Theme = 'light' | 'dark' | 'system';

type ThemeState = {
  theme: Theme;
  setTheme: (theme: Theme) => void;
};

const STORAGE_KEY = 'docfusion-theme';

function getStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  } catch {
    /* ignore */
  }
  return 'system';
}

function resolveEffective(theme: Theme): 'light' | 'dark' {
  if (theme !== 'system') return theme;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyToDOM(theme: Theme) {
  const effective = resolveEffective(theme);
  document.documentElement.classList.toggle('dark', effective === 'dark');
}

export const useThemeStore = create<ThemeState>((set) => ({
  theme: getStoredTheme(),
  setTheme: (theme) => {
    localStorage.setItem(STORAGE_KEY, theme);
    applyToDOM(theme);
    set({ theme });
  },
}));

/** 在应用启动时调用一次，同步 DOM class 并监听系统主题变化 */
export function initTheme() {
  const { theme } = useThemeStore.getState();
  applyToDOM(theme);

  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    const current = useThemeStore.getState().theme;
    if (current === 'system') applyToDOM('system');
  });
}
