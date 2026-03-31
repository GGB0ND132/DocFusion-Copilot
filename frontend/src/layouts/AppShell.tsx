import { Link, Outlet, useLocation } from 'react-router-dom';
import { Layers, MessageSquare } from 'lucide-react';
import { cn } from '@/lib/utils';

const NAV_ITEMS = [
  { to: '/workspace', label: '工作台', icon: Layers },
  { to: '/agent', label: 'Agent', icon: MessageSquare },
] as const;

export default function AppShell() {
  const { pathname } = useLocation();

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
      </aside>
      {/* Main area */}
      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
