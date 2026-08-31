import type { ComponentType } from 'react';

interface StatCardProps {
  label: string;
  value: string;
  icon: ComponentType<{ size?: number; strokeWidth?: number; className?: string }>;
  tone: 'indigo' | 'emerald' | 'amber';
}

const TONES = {
  indigo: 'bg-purple-500/10 text-purple-300',
  emerald: 'bg-emerald-500/10 text-emerald-300',
  amber: 'bg-amber-500/10 text-amber-300',
};

export function StatCard({ label, value, icon: Icon, tone }: StatCardProps) {
  return (
    <div className="neon-border neon-border-hover flex items-center gap-4 rounded-2xl bg-slate-900/50 p-5 transition">
      <div className={`flex h-11 w-11 items-center justify-center rounded-xl ${TONES[tone]}`}>
        <Icon size={20} strokeWidth={2} />
      </div>
      <div>
        <p className="text-2xl font-semibold text-white">{value}</p>
        <p className="text-sm text-slate-400">{label}</p>
      </div>
    </div>
  );
}
