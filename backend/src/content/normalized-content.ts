export interface NormalizedContent {
  source: string;
  sourceId: string;
  sourceUrl: string;
  title: string;
  description: string;
  author: string;
  publishedAt: string;
  mediaType: 'video';
  mediaReference: string;
  thumbnail: string | null;
  metadata: Record<string, unknown>;
}

export function contentIdentity(source: string, sourceId: string): string {
  return `${source}:${sourceId}`;
}
