import { BadRequestException, Injectable, Logger } from '@nestjs/common';
import { chromium, type BrowserContext, type Page } from 'playwright';
import { join } from 'node:path';
import { SessionStoreService } from './session-store.service.js';

const LOGIN_URLS: Record<string, string> = {
  tiktok: 'https://www.tiktok.com/login',
};

const PROFILE_DIR: Record<string, string> = {
  tiktok: join(process.cwd(), 'data', 'chrome-profile-tiktok', 'User Data', 'Profile 1'),
};

interface PendingLogin {
  context: BrowserContext;
  page: Page;
}

/**
 * Dung 1 ban sao (da bo cache) cua profile Chrome that cua nguoi dung, luu rieng trong thu muc
 * cua tool - khong dung chung khoa voi Chrome dang chay hang ngay cua ho nen khong can dong Chrome
 * moi lan dang nhap lai. Chi chay duoc tren may co man hinh that (Huong C).
 */
@Injectable()
export class PlatformLoginService {
  private readonly logger = new Logger(PlatformLoginService.name);
  private readonly pending = new Map<string, PendingLogin>();

  constructor(private readonly sessionStore: SessionStoreService) {}

  async startLogin(platform: string): Promise<{ started: boolean }> {
    const url = LOGIN_URLS[platform];
    const profileDir = PROFILE_DIR[platform];
    if (!url || !profileDir) {
      throw new BadRequestException(`Chua ho tro platform "${platform}".`);
    }
    if (this.pending.has(platform)) {
      throw new BadRequestException(`Da co cua so dang nhap "${platform}" dang mo, dong lai (Hoan tat/Huy) truoc.`);
    }

    const context = await chromium.launchPersistentContext(profileDir, {
      headless: false,
      channel: 'chrome',
      viewport: null,
      args: ['--disable-blink-features=AutomationControlled'],
    });
    const page = context.pages()[0] ?? (await context.newPage());
    await page.goto(url, { waitUntil: 'domcontentloaded' }).catch((err) => {
      this.logger.warn(`Mo trang login ${platform} loi: ${err?.message}`);
    });

    this.pending.set(platform, { context, page });
    return { started: true };
  }

  async finishLogin(platform: string) {
    const pending = this.pending.get(platform);
    if (!pending) {
      throw new BadRequestException(`Khong co cua so dang nhap "${platform}" nao dang mo.`);
    }
    this.pending.delete(platform);

    try {
      const meta = await this.sessionStore.saveFromContext(platform, pending.context);
      return meta;
    } finally {
      await pending.context.close().catch(() => undefined);
    }
  }

  async cancelLogin(platform: string) {
    const pending = this.pending.get(platform);
    if (!pending) return { cancelled: false };
    this.pending.delete(platform);
    await pending.context.close().catch(() => undefined);
    return { cancelled: true };
  }

  isPending(platform: string): boolean {
    return this.pending.has(platform);
  }
}
