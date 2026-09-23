import type { StageStatus } from '../lib/api'
import StatusBadge from './StatusBadge'

export interface EpisodeSidebarItem {
  id: string
  label: string
  overallStatus: StageStatus
}

export interface StageSidebarItem {
  id: string
  label: string
  status?: StageStatus
}

function scrollToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function EpisodesNav({
  episodes,
  selectedId,
  onSelect,
}: {
  episodes: EpisodeSidebarItem[]
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  return (
    <nav className="flex flex-col gap-0.5">
      {episodes.map((ep, i) => (
        <button
          key={ep.id}
          type="button"
          onClick={() => onSelect(ep.id)}
          className={[
            'flex items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm transition-colors',
            ep.id === selectedId ? 'bg-accent-900/70 text-accent-200' : 'text-neutral-300 hover:bg-accent-900/25',
          ].join(' ')}
        >
          <span className="w-5 shrink-0 font-mono text-xs text-neutral-500">{i + 1}.</span>
          <span className="min-w-0 flex-1 truncate">{ep.label}</span>
          <StatusBadge status={ep.overallStatus} />
        </button>
      ))}
    </nav>
  )
}

function StagesNav({ stages }: { stages: StageSidebarItem[] }) {
  return (
    <nav className="flex flex-col gap-0.5">
      {stages.map((s) => (
        <button
          key={s.id}
          type="button"
          onClick={() => scrollToSection(s.id)}
          className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm text-neutral-300 transition-colors hover:bg-accent-900/25"
        >
          <span className="min-w-0 flex-1 truncate">{s.label}</span>
          {s.status && <StatusBadge status={s.status} />}
        </button>
      ))}
    </nav>
  )
}

export default function ProjectSidebar(
  props:
    | { mode: 'episodes'; episodes: EpisodeSidebarItem[]; selectedId: string | null; onSelect: (id: string) => void; title: string }
    | { mode: 'stages'; stages: StageSidebarItem[]; title: string },
) {
  return (
    <aside className="sticky top-6 hidden w-64 shrink-0 self-start rounded-lg border border-divider bg-surface p-3 lg:block">
      <p className="mb-2 px-2 text-xs font-medium tracking-wide text-neutral-500 uppercase">{props.title}</p>
      {props.mode === 'episodes' ? (
        <EpisodesNav episodes={props.episodes} selectedId={props.selectedId} onSelect={props.onSelect} />
      ) : (
        <StagesNav stages={props.stages} />
      )}
    </aside>
  )
}
