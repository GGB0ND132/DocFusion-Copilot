import { Link, Outlet, useLocation } from 'react-router-dom';
import { Layers, MessageSquare, Moon, Sun, Monitor } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useThemeStore } from '@/stores/themeStore';

const NAV_ITEMS = [
  { to: '/workspace', label: '工作台', icon: Layers },
  { to: '/agent', label: 'Agent', icon: MessageSquare },
] as const;

const THEME_CYCLE = ['light', 'dark', 'system'] as const;
const THEME_ICON = { light: Sun, dark: Moon, system: Monitor } as const;
const THEME_LABEL = { light: '浅色', dark: '深色', system: '跟随系统' } as const;

export default function AppShell() {
  const { pathname } = useLocation();
  const { theme, setTheme } = useThemeStore();

  const cycleTheme = () => {
    const idx = THEME_CYCLE.indexOf(theme);
    setTheme(THEME_CYCLE[(idx + 1) % THEME_CYCLE.length]);
  };

  const ThemeIcon = THEME_ICON[theme];

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Sidebar */}
      <aside className="flex w-14 flex-col items-center border-r bg-card py-4 gap-1">
        <div className="mb-4 text-xs font-bold text-primary tracking-tighter">DF</div>
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <Link
            key={to}
            to={to}
            className={cn(
              'flex flex-col items-center gap-0.5 rounded-md p-2 text-[10px] transition-colors hover:bg-muted',
              pathname.startsWith(to) ? 'bg-muted text-primary font-medium' : 'text-muted-foreground',
            )}
          >
            <Icon className="h-5 w-5" />
            {label}
          </Link>
        ))}
        {/* Spacer */}
        <div className="flex-1" />
        {/* Theme toggle */}
        <button
          onClick={cycleTheme}
          title={THEME_LABEL[theme]}
          className="flex flex-col items-center gap-0.5 rounded-md p-2 text-[10px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <ThemeIcon className="h-5 w-5" />
          {THEME_LABEL[theme]}
        </button>
      </aside>
      {/* Main area */}
      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
