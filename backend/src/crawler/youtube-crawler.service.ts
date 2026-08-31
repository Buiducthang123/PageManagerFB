import { Injectable, Logger } from '@nestjs/common';
import { chromium } from 'playwright';
import type { NormalizedContent } from '../content/normalized-content.js';

/** Cac buoc filter thu tu uu tien - giong tinh than fallback keyword (muc 5): het "Today" moi roi qua "This week". */
const DATE_FILTERS = ['Today', 'This week', 'This month'];

@Injectable()
export class YouTubeCrawlerService {
  private readonly logger = new Logger(YouTubeCrawlerService.name);

  /** Tim YouTube Shorts theo keyword, uu tien video moi nhat co the. */
  async searchShorts(keyword: string, limit = 12): Promise<NormalizedContent[]> {
    const browser = await chromium.launch({ headless: true });
    try {
      const page = await browser.newPage({
        userAgent:
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36',
      });

      const params = new URLSearchParams({ search_query: keyword, hl: 'en', gl: 'US' });
      await page.goto(`https://www.youtube.com/results?${params.toString()}`, {
        waitUntil: 'domcontentloaded',
        timeout: 30000,
      });
      await page.waitForTimeout(2500);
      await this.dismissConsent(page);
      await page.waitForSelector('ytd-video-renderer', { timeout: 15000 }).catch(() => null);

      // Ap filter "Type: Shorts"
      await this.clickFilter(page, 'Shorts');
      await page.waitForTimeout(2000);

      let items: NormalizedContent[] = [];
      for (const dateFilter of DATE_FILTERS) {
        await this.clickFilter(page, dateFilter);
        await page.waitForTimeout(2000);
        items = await this.extractShorts(page, limit);
        if (items.length > 0) {
          this.logger.log(`YouTube "${keyword}": tim thay ${items.length} Shorts voi filter "${dateFilter}".`);
          return items;
        }
      }

      // Het ca 3 moc thoi gian van rong - bo loc ngay, chi giu loai Shorts, tranh tra ve rong oan
      // khi keyword co Shorts nhung khong co ban nao that su moi.
      await this.clickFilter(page, 'Shorts');
      await page.waitForTimeout(2000);
      items = await this.extractShorts(page, limit);
      this.logger.log(
        `YouTube "${keyword}": khong co Shorts moi trong thang, fallback ve khong loc ngay - tim thay ${items.length}.`,
      );
      return items;
    } finally {
      await browser.close().catch(() => undefined);
    }
  }

  private async dismissConsent(page: import('playwright').Page) {
    try {
      const btn = page.locator('button:has-text("Accept all")').first();
      if (await btn.isVisible({ timeout: 2000 })) {
        await btn.click();
        await page.waitForTimeout(1000);
      }
    } catch {
      // khong co consent dialog, bo qua
    }
  }

  private async clickFilter(page: import('playwright').Page, label: string) {
    try {
      await page.locator('button[aria-label="Search filters"]').first().click({ timeout: 8000 });
      await page.waitForTimeout(600);
      await page
        .locator('ytd-search-filter-renderer', { hasText: label })
        .first()
        .locator('a')
        .click({ timeout: 8000 });
    } catch (err: any) {
      this.logger.warn(`Khong click duoc filter "${label}": ${err?.message}`);
    }
  }

  private async extractShorts(page: import('playwright').Page, limit: number): Promise<NormalizedContent[]> {
    const raw = await page.evaluate((max) => {
      // Dinh dang 1: luoi Shorts rieng (tu khoa pho bien, nhieu ket qua)
      const gridNodes = Array.from(document.querySelectorAll('ytm-shorts-lockup-view-model-v2'));
      if (gridNodes.length > 0) {
        return gridNodes.slice(0, max).map((n) => {
          const a = n.querySelector('a[href^="/shorts/"]');
          const titleEl = n.querySelector('h3, .shortsLockupViewModelHostMetadataTitle');
          const metaEl = n.querySelector(
            '.shortsLockupViewModelHostMetadataSubhead, .shortsLockupViewModelHostOutsideMetadataSubhead',
          );
          const img = n.querySelector('img');
          return {
            href: a?.getAttribute('href') ?? null,
            title: titleEl?.textContent?.trim() ?? '',
            viewsText: metaEl?.textContent?.trim() ?? '',
            thumb: img?.getAttribute('src') ?? null,
          };
        });
      }

      // Dinh dang 2: tu khoa it pho bien hon, YouTube tra Shorts lan trong ytd-video-renderer thuong
      const videoNodes = Array.from(document.querySelectorAll('ytd-video-renderer'));
      return videoNodes
        .map((n) => {
          const a = n.querySelector('#video-title');
          const href = a?.getAttribute('href') ?? '';
          if (!href.startsWith('/shorts/')) return null;
          const metaEl = n.querySelector('#metadata-line');
          const img = n.querySelector('img');
          return {
            href,
            title: a?.textContent?.trim() ?? '',
            viewsText: metaEl?.textContent?.trim().replace(/\s+/g, ' ') ?? '',
            thumb: img?.getAttribute('src') ?? null,
          };
        })
        .filter((x): x is NonNullable<typeof x> => x !== null)
        .slice(0, max);
    }, limit);

    const seen = new Set<string>();
    const results: NormalizedContent[] = [];
    for (const item of raw) {
      const videoId = item.href?.split('/shorts/')[1]?.split('?')[0];
      if (!videoId || seen.has(videoId) || !item.title) continue;
      seen.add(videoId);
      results.push({
        source: 'youtube',
        sourceId: videoId,
        sourceUrl: `https://www.youtube.com/shorts/${videoId}`,
        title: item.title,
        description: '',
        author: '',
        publishedAt: '',
        mediaType: 'video',
        mediaReference: `https://www.youtube.com/shorts/${videoId}`,
        thumbnail: item.thumb,
        metadata: { viewsText: item.viewsText },
      });
    }
    return results;
  }
}
