export function toAbsoluteFacebookUrl(url: string): string {
  if (url.startsWith('http://') || url.startsWith('https://')) return url;
  return `https://www.facebook.com${url.startsWith('/') ? url : `/${url}`}`;
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return '';
  const s = Math.round(seconds);
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return `${m}:${rest.toString().padStart(2, '0')}`;
}
