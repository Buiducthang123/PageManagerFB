import { Injectable, Logger } from '@nestjs/common';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { contentIdentity, type NormalizedContent } from './normalized-content.js';

export interface PoolItem extends NormalizedContent {
  discoveredAt: string;
}

interface StoreFile {
  items: PoolItem[];
}

const STORE_PATH = join(process.cwd(), 'data', 'content-pool.json');

/**
 * Global Content Pool (muc 10 trong plan) - luu content da crawl tu cac source,
 * dung chung cho nhieu Page. Identity toi thieu la source+sourceId (muc 8).
 */
@Injectable()
export class ContentPoolStore {
  private readonly logger = new Logger(ContentPoolStore.name);

  async getAll(): Promise<PoolItem[]> {
    const store = await this.readStore();
    return store.items;
  }

  async knownIdentities(): Promise<Set<string>> {
    const store = await this.readStore();
    return new Set(store.items.map((i) => contentIdentity(i.source, i.sourceId)));
  }

  /** Them cac content moi vao pool, bo qua content da biet (khong ghi de). Tra ve so luong moi them. */
  async addNew(items: NormalizedContent[]): Promise<{ added: number; duplicates: number }> {
    const store = await this.readStore();
    const known = new Set(store.items.map((i) => contentIdentity(i.source, i.sourceId)));

    let added = 0;
    let duplicates = 0;
    for (const item of items) {
      const id = contentIdentity(item.source, item.sourceId);
      if (known.has(id)) {
        duplicates++;
        continue;
      }
      known.add(id);
      store.items.push({ ...item, discoveredAt: new Date().toISOString() });
      added++;
    }

    if (added > 0) {
      await this.writeStore(store);
    }
    this.logger.log(`Content pool: +${added} moi, ${duplicates} da biet (bo qua).`);
    return { added, duplicates };
  }

  private async readStore(): Promise<StoreFile> {
    try {
      const raw = await readFile(STORE_PATH, 'utf-8');
      return JSON.parse(raw) as StoreFile;
    } catch {
      return { items: [] };
    }
  }

  private async writeStore(store: StoreFile): Promise<void> {
    await mkdir(dirname(STORE_PATH), { recursive: true });
    await writeFile(STORE_PATH, JSON.stringify(store, null, 2), 'utf-8');
  }
}
