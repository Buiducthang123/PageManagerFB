import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';

/** Nhom tu khoa theo thu tu uu tien: keywordGroups[0] la uu tien P1, [1] la P2,... (muc 5, muc 31 trong plan). */
export interface ContentProfile {
  keywordGroups: string[][];
}

export interface StoredPage {
  pageId: string;
  pageName: string;
  category: string | null;
  tasks: string[];
  pageAccessToken: string;
  pictureUrl: string | null;
  connectedAt: string;
  contentProfile?: ContentProfile;
}

interface StoreFile {
  pages: StoredPage[];
}

const STORE_PATH = join(process.cwd(), 'data', 'pages.json');

@Injectable()
export class PageStoreService {
  private readonly logger = new Logger(PageStoreService.name);

  async getAll(): Promise<StoredPage[]> {
    const store = await this.readStore();
    return store.pages;
  }

  async getById(pageId: string): Promise<StoredPage> {
    const store = await this.readStore();
    const page = store.pages.find((p) => p.pageId === pageId);
    if (!page) {
      throw new NotFoundException(`Khong tim thay page ${pageId} trong store.`);
    }
    return page;
  }

  async remove(pageId: string): Promise<void> {
    const store = await this.readStore();
    const before = store.pages.length;
    store.pages = store.pages.filter((p) => p.pageId !== pageId);
    if (store.pages.length === before) {
      throw new NotFoundException(`Khong tim thay page ${pageId} trong store.`);
    }
    await this.writeStore(store);
    this.logger.log(`Da xoa page ${pageId} khoi store.`);
  }

  /** Merge (khong ghi de) de giu lai cac field rieng cua tool nhu contentProfile khi reconnect OAuth. */
  async upsertMany(pages: StoredPage[]): Promise<void> {
    const store = await this.readStore();
    const byId = new Map(store.pages.map((p) => [p.pageId, p]));
    for (const page of pages) {
      const existing = byId.get(page.pageId);
      byId.set(page.pageId, existing ? { ...existing, ...page } : page);
    }
    store.pages = Array.from(byId.values());
    await this.writeStore(store);
    this.logger.log(`Saved ${pages.length} page(s), tong cong ${store.pages.length} page trong store.`);
  }

  async updateContentProfile(pageId: string, profile: ContentProfile): Promise<StoredPage> {
    const store = await this.readStore();
    const page = store.pages.find((p) => p.pageId === pageId);
    if (!page) {
      throw new NotFoundException(`Khong tim thay page ${pageId} trong store.`);
    }
    page.contentProfile = profile;
    await this.writeStore(store);
    return page;
  }

  private async readStore(): Promise<StoreFile> {
    try {
      const raw = await readFile(STORE_PATH, 'utf-8');
      return JSON.parse(raw) as StoreFile;
    } catch {
      return { pages: [] };
    }
  }

  private async writeStore(store: StoreFile): Promise<void> {
    await mkdir(dirname(STORE_PATH), { recursive: true });
    await writeFile(STORE_PATH, JSON.stringify(store, null, 2), 'utf-8');
  }
}
