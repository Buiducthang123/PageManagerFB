import {
  BarChart3,
  CalendarClock,
  FileText,
  LayoutDashboard,
  ListChecks,
  Plug,
  Settings,
  Store,
  TriangleAlert,
  Zap,
} from 'lucide-react';
import type { ComponentType } from 'react';
import { NavLink } from 'react-router-dom';

interface NavItem {
  label: string;
  icon: ComponentType<{ size?: number; strokeWidth?: number; className?: string }>;
  to?: string;
  soon?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Bảng điều khiển', icon: LayoutDashboard, soon: true },
  { label: 'Trang', icon: Store, to: '/' },
  { label: 'Nền tảng', icon: Plug, to: '/platforms' },
  { label: 'Nội dung', icon: FileText, to: '/content' },
  { label: 'Hàng đợi', icon: ListChecks, soon: true },
  { label: 'Lịch đăng', icon: CalendarClock, soon: true },
  { label: 'Tự động hoá', icon: Zap, soon: true },
  { label: 'Lỗi', icon: TriangleAlert, soon: true },
  { label: 'Phân tích', icon: BarChart3, soon: true },
];

export function Sidebar() {
  return (
    <aside className="flex h-screen w-64 shrink-0 flex-col border-r border-purple-500/20 bg-black/60 text-slate-300">
      <div className="flex items-center gap-2.5 px-6 py-6">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-purple-500 to-fuchsia-600 text-sm font-bold text-white shadow-[0_0_16px_rgba(192,38,211,0.45)]">
          P
        </div>
        <div>
          <p className="text-sm font-semibold text-white">Pages Manager</p>
          <p className="text-xs text-slate-500">Tự động hoá nội dung</p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 px-3">
        {NAV_ITEMS.map(({ label, icon: Icon, to, soon }) => {
          const content = (
            <>
              <span className="flex items-center gap-2.5">
                <Icon size={17} strokeWidth={2} />
                {label}
              </span>
              {soon && (
                <span className="rounded-full bg-slate-800/80 px-1.5 py-0.5 text-[10px] tracking-wide text-slate-500">
                  sắp có
                </span>
              )}
            </>
          );

          if (soon || !to) {
            return (
              <button
                key={label}
                disabled
                className="group flex w-full cursor-not-allowed items-center justify-between rounded-lg border border-transparent px-3 py-2 text-sm text-slate-600"
              >
                {content}
              </button>
            );
          }

          return (
            <NavLink
              key={label}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                [
                  'group flex w-full items-center justify-between rounded-lg border px-3 py-2 text-sm transition',
                  isActive
                    ? 'border-purple-500/40 bg-purple-500/10 font-medium text-purple-300 shadow-[0_0_14px_rgba(192,38,211,0.15)]'
                    : 'border-transparent text-slate-300 hover:border-purple-500/20 hover:bg-white/5',
                ].join(' ')
              }
            >
              {content}
            </NavLink>
          );
        })}
      </nav>

      <div className="flex items-center gap-2.5 border-t border-purple-500/15 px-6 py-4">
        <Settings size={16} className="text-slate-500" />
        <span className="text-xs text-slate-500">Giai đoạn 0 &middot; Kiểm chứng kỹ thuật</span>
      </div>
    </aside>
  );
}
