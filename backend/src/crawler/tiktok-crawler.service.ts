import { BadRequestException, Injectable, Logger } from '@nestjs/common';
import { chromium } from 'playwright';
import { join } from 'node:path';
import type { NormalizedContent } from '../content/normalized-content.js';
import { SessionStoreService } from '../platforms/session-store.service.js';

const PROFILE_DIR = join(process.cwd(), 'data', 'chrome-profile-tiktok', 'User Data', 'Profile 1');

@Injectable()
export class TikTokCrawlerService {
  private readonly logger = new Logger(TikTokCrawlerService.name);

  constructor(private readonly sessionStore: SessionStoreService) {}

  async searchVideos(keyword: string, limit = 12): Promise<NormalizedContent[]> {
    const hasSession = await this.sessionStore.hasSessionFile('tiktok');
    if (!hasSession) {
      throw new BadRequestException('Chua co session TikTok - vao trang Nen tang de dang nhap truoc.');
    }

    // Quan trong: phai dung profile Chrome that (persistent context) - chi nap cookie vao browser
    // trang thi TikTok van phat hien duoc dau la headless va chan.
    const context = await chromium.launchPersistentContext(PROFILE_DIR, {
      headless: true,
      channel: 'chrome',
      args: ['--disable-blink-features=AutomationControlled'],
    });
    try {
      const page = context.pages()[0] ?? (await context.newPage());
      await page.goto(`https://www.tiktok.com/search/video?q=${encodeURIComponent(keyword)}`, {
        waitUntil: 'domcontentloaded',
        timeout: 30000,
      });
      await page.waitForTimeout(4000);

      const blocked = await page.locator('text=Something went wrong').isVisible({ timeout: 2000 }).catch(() => false);
      if (blocked) {
        throw new BadRequestException(
          'TikTok chan request nay (session co the da het han) - vao Nen tang dang nhap lai.',
        );
      }

      const raw = await page.evaluate((max) => {
        const anchors = Array.from(document.querySelectorAll('a[href*="/video/"]'));
        const seen = new Set<string>();
        const out: Array<{ href: string; title: string; thumb: string | null }> = [];
        for (const a of anchors) {
          const href = a.getAttribute('href') ?? '';
          if (!href || seen.has(href)) continue;
          seen.add(href);
          const container = a.closest('div');
          const img = container?.querySelector('img');
          const desc = container?.querySelector('[data-e2e="search-card-desc"], [class*="Description"]');
          out.push({ href, title: desc?.textContent?.trim() ?? '', thumb: img?.getAttribute('src') ?? null });
          if (out.length >= max) break;
        }
        return out;
      }, limit);

      return raw
        .map((item) => {
          const match = item.href.match(/\/video\/(\d+)/);
          const videoId = match?.[1];
          if (!videoId) return null;
          const content: NormalizedContent = {
            source: 'tiktok',
            sourceId: videoId,
            sourceUrl: item.href.startsWith('http') ? item.href : `https://www.tiktok.com${item.href}`,
            title: item.title,
            description: '',
            author: '',
            publishedAt: '',
            mediaType: 'video',
            mediaReference: item.href.startsWith('http') ? item.href : `https://www.tiktok.com${item.href}`,
            thumbnail: item.thumb,
            metadata: {},
          };
          return content;
        })
        .filter((x): x is NormalizedContent => x !== null);
    } finally {
      await context.close().catch(() => undefined);
    }
  }
}
