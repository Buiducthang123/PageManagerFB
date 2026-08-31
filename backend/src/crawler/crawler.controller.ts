import { BadRequestException, Body, Controller, Post } from '@nestjs/common';
import { ContentPoolStore } from '../content/content-pool.store.js';
import type { NormalizedContent } from '../content/normalized-content.js';
import { TikTokCrawlerService } from './tiktok-crawler.service.js';
import { YouTubeCrawlerService } from './youtube-crawler.service.js';

@Controller('crawler')
export class CrawlerController {
  constructor(
    private readonly youtube: YouTubeCrawlerService,
    private readonly tiktok: TikTokCrawlerService,
    private readonly pool: ContentPoolStore,
  ) {}

  /** Test search truc tiep, KHONG luu vao pool - de xem UI ket qua truoc. */
  @Post('search')
  async search(
    @Body('source') source: string,
    @Body('keyword') keyword: string,
    @Body('limit') limit?: number,
  ) {
    if (!keyword?.trim()) {
      throw new BadRequestException('Thieu keyword.');
    }

    let results: NormalizedContent[];
    if (source === 'youtube') {
      results = await this.youtube.searchShorts(keyword.trim(), limit ?? 12);
    } else if (source === 'tiktok') {
      results = await this.tiktok.searchVideos(keyword.trim(), limit ?? 12);
    } else {
      throw new BadRequestException('source phai la "youtube" hoac "tiktok".');
    }

    const known = await this.pool.knownIdentities();
    return results.map((r) => ({ ...r, isNew: !known.has(`${r.source}:${r.sourceId}`) }));
  }

  /** Search + luu ngay vao Content Pool (dedup tu dong). */
  @Post('search-and-save')
  async searchAndSave(
    @Body('source') source: string,
    @Body('keyword') keyword: string,
    @Body('limit') limit?: number,
  ) {
    if (!keyword?.trim()) {
      throw new BadRequestException('Thieu keyword.');
    }

    const results =
      source === 'youtube'
        ? await this.youtube.searchShorts(keyword.trim(), limit ?? 12)
        : source === 'tiktok'
          ? await this.tiktok.searchVideos(keyword.trim(), limit ?? 12)
          : (() => {
              throw new BadRequestException('source phai la "youtube" hoac "tiktok".');
            })();

    const { added, duplicates } = await this.pool.addNew(results);
    return { found: results.length, added, duplicates };
  }
}
