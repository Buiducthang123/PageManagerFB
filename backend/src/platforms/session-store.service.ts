import { Injectable } from '@nestjs/common';
import type { BrowserContext, Cookie } from 'playwright';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';

export interface PlatformSessionMeta {
  platform: string;
  loggedInAt: string;
  cookieCount: number;
}

const SESSION_DIR = join(process.cwd(), 'data', 'sessions');

function sessionPath(platform: string) {
  return join(SESSION_DIR, `${platform}.json`);
}

function metaPath(platform: string) {
  return join(SESSION_DIR, `${platform}.meta.json`);
}

@Injectable()
export class SessionStoreService {
  async saveFromContext(platform: string, context: BrowserContext): Promise<PlatformSessionMeta> {
    await mkdir(SESSION_DIR, { recursive: true });
    const state = await context.storageState();
    await writeFile(sessionPath(platform), JSON.stringify(state, null, 2), 'utf-8');
    const meta: PlatformSessionMeta = {
      platform,
      loggedInAt: new Date().toISOString(),
      cookieCount: state.cookies.length,
    };
    await writeFile(metaPath(platform), JSON.stringify(meta, null, 2), 'utf-8');
    return meta;
  }

  /** Luu tap cookie (lay tu context.cookies() khi ket noi qua CDP vao Chrome that) duoi dang storageState. */
  async saveCookies(platform: string, cookies: Cookie[]): Promise<PlatformSessionMeta> {
    await mkdir(SESSION_DIR, { recursive: true });
    const state = { cookies, origins: [] as unknown[] };
    await writeFile(sessionPath(platform), JSON.stringify(state, null, 2), 'utf-8');
    const meta: PlatformSessionMeta = {
      platform,
      loggedInAt: new Date().toISOString(),
      cookieCount: cookies.length,
    };
    await writeFile(metaPath(platform), JSON.stringify(meta, null, 2), 'utf-8');
    return meta;
  }

  async getMeta(platform: string): Promise<PlatformSessionMeta | null> {
    try {
      const raw = await readFile(metaPath(platform), 'utf-8');
      return JSON.parse(raw) as PlatformSessionMeta;
    } catch {
      return null;
    }
  }

  hasSessionFile(platform: string): Promise<boolean> {
    return readFile(sessionPath(platform))
      .then(() => true)
      .catch(() => false);
  }

  storageStatePath(platform: string): string {
    return sessionPath(platform);
  }
}
