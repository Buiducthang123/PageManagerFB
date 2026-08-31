import type {
  ConnectedPage,
  ContentProfile,
  DiscoveredContent,
  PageStats,
  PageVideo,
  PlatformStatus,
  ReelStatus,
  SearchAndSaveResult,
  TestPostResult,
  TestReelResult,
} from './types';

const API_BASE = '/api';

export const facebookLoginUrl = `${API_BASE}/facebook/login`;

async function parseOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.message ?? `Loi HTTP ${res.status}`);
  }
  return res.json();
}

export async function fetchConnectedPages(): Promise<ConnectedPage[]> {
  const res = await fetch(`${API_BASE}/facebook/pages`);
  return parseOrThrow(res);
}

export async function fetchPage(pageId: string): Promise<ConnectedPage> {
  const res = await fetch(`${API_BASE}/facebook/pages/${pageId}`);
  return parseOrThrow(res);
}

export async function disconnectPage(pageId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/facebook/pages/${pageId}`, { method: 'DELETE' });
  await parseOrThrow(res);
}

export async function deletePost(pageId: string, postId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/facebook/pages/${pageId}/posts/${postId}`, { method: 'DELETE' });
  await parseOrThrow(res);
}

export async function fetchPageStats(pageId: string): Promise<PageStats> {
  const res = await fetch(`${API_BASE}/facebook/pages/${pageId}/stats`);
  return parseOrThrow(res);
}

export async function createTestPost(pageId: string, message: string): Promise<TestPostResult> {
  const res = await fetch(`${API_BASE}/facebook/pages/${pageId}/test-post`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });
  return parseOrThrow(res);
}

export async function submitTestReel(
  pageId: string,
  file: File,
  description: string,
): Promise<TestReelResult> {
  const form = new FormData();
  form.append('video', file);
  form.append('description', description);
  const res = await fetch(`${API_BASE}/facebook/pages/${pageId}/test-reel`, {
    method: 'POST',
    body: form,
  });
  return parseOrThrow(res);
}

export async function fetchReelStatus(pageId: string, videoId: string): Promise<ReelStatus> {
  const res = await fetch(`${API_BASE}/facebook/pages/${pageId}/reel-status/${videoId}`);
  return parseOrThrow(res);
}

export async function fetchPageVideos(pageId: string): Promise<PageVideo[]> {
  const res = await fetch(`${API_BASE}/facebook/pages/${pageId}/videos`);
  return parseOrThrow(res);
}

export async function fetchContentProfile(pageId: string): Promise<ContentProfile> {
  const res = await fetch(`${API_BASE}/facebook/pages/${pageId}/content-profile`);
  return parseOrThrow(res);
}

export async function saveContentProfile(pageId: string, profile: ContentProfile): Promise<ContentProfile> {
  const res = await fetch(`${API_BASE}/facebook/pages/${pageId}/content-profile`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  });
  return parseOrThrow(res);
}

export async function fetchPlatforms(): Promise<PlatformStatus[]> {
  const res = await fetch(`${API_BASE}/platforms`);
  return parseOrThrow(res);
}

export async function startPlatformLogin(platform: string): Promise<{ started: boolean }> {
  const res = await fetch(`${API_BASE}/platforms/${platform}/login/start`, { method: 'POST' });
  return parseOrThrow(res);
}

export async function finishPlatformLogin(platform: string) {
  const res = await fetch(`${API_BASE}/platforms/${platform}/login/finish`, { method: 'POST' });
  return parseOrThrow(res);
}

export async function cancelPlatformLogin(platform: string) {
  const res = await fetch(`${API_BASE}/platforms/${platform}/login/cancel`, { method: 'POST' });
  return parseOrThrow(res);
}

export async function searchContent(
  source: string,
  keyword: string,
  limit = 12,
): Promise<DiscoveredContent[]> {
  const res = await fetch(`${API_BASE}/crawler/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, keyword, limit }),
  });
  return parseOrThrow(res);
}

export async function searchAndSaveContent(
  source: string,
  keyword: string,
  limit = 12,
): Promise<SearchAndSaveResult> {
  const res = await fetch(`${API_BASE}/crawler/search-and-save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, keyword, limit }),
  });
  return parseOrThrow(res);
}
