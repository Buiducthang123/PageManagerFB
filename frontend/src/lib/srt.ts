// Mirror chính xác app/utils/srt.py::parse_ts — format "HH:MM:SS,mmm" (hoặc
// "." thay ",") — để FE tua video tới đúng giây khớp với timestamp backend
// đã ghi trong .srt.
export function srtTimeToSeconds(ts: string): number {
  const cleaned = ts.trim().replace('.', ',')
  const [hms, ms = '0'] = cleaned.split(',')
  const parts = hms.split(':').map(Number)
  if (parts.length !== 3 || parts.some((p) => Number.isNaN(p))) return 0
  const [h, m, s] = parts
  const msNum = Number(ms.padEnd(3, '0').slice(0, 3)) || 0
  return h * 3600 + m * 60 + s + msNum / 1000
}
