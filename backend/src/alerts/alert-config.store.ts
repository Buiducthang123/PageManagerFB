import { Injectable } from '@nestjs/common';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';

export interface AlertConfig {
  pageId: string | null;
  recipientPsid: string | null;
  recipientName: string | null;
  updatedAt: string | null;
}

const STORE_PATH = join(process.cwd(), 'data', 'alert-config.json');
const EMPTY: AlertConfig = { pageId: null, recipientPsid: null, recipientName: null, updatedAt: null };

@Injectable()
export class AlertConfigStore {
  async get(): Promise<AlertConfig> {
    try {
      const raw = await readFile(STORE_PATH, 'utf-8');
      return JSON.parse(raw) as AlertConfig;
    } catch {
      return EMPTY;
    }
  }

  async save(config: AlertConfig): Promise<void> {
    await mkdir(dirname(STORE_PATH), { recursive: true });
    await writeFile(STORE_PATH, JSON.stringify(config, null, 2), 'utf-8');
  }
}
