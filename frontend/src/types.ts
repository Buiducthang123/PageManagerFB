export interface ConnectedPage {
  pageId: string;
  pageName: string;
  category: string | null;
  tasks: string[];
  pictureUrl: string | null;
  connectedAt: string;
  pageAccessToken: string;
}

export interface RecentPost {
  id: string;
  message: string;
  createdAt: string;
  permalinkUrl: string | null;
  likeCount: number;
  commentCount: number;
  shareCount: number;
}

export interface PageStats {
  followers: number | null;
  about: string | null;
  link: string | null;
  recentPosts: RecentPost[];
}

export interface TestPostResult {
  postId: string;
  permalinkUrl: string;
}

export interface TestReelResult {
  videoId: string;
  message: string;
  checkStatusUrl: string;
}

export interface ReelStatus {
  videoId: string;
  status: unknown;
  permalinkUrl: string | null;
  thumbnailUrl: string | null;
  length: number | null;
}

export interface PageVideo {
  id: string;
  description: string;
  updatedAt: string;
  permalinkUrl: string | null;
  thumbnailUrl: string | null;
  length: number | null;
}

export interface ContentProfile {
  keywordGroups: string[][];
}

export interface PlatformStatus {
  platform: string;
  loggedIn: boolean;
  loggedInAt: string | null;
  loginWindowOpen: boolean;
}

export interface DiscoveredContent {
  source: string;
  sourceId: string;
  sourceUrl: string;
  title: string;
  description: string;
  author: string;
  publishedAt: string;
  mediaType: string;
  mediaReference: string;
  thumbnail: string | null;
  metadata: Record<string, unknown>;
  isNew: boolean;
}

export interface SearchAndSaveResult {
  found: number;
  added: number;
  duplicates: number;
}
