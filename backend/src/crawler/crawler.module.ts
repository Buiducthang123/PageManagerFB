import { Module } from '@nestjs/common';
import { ContentPoolStore } from '../content/content-pool.store.js';
import { PlatformsModule } from '../platforms/platforms.module.js';
import { CrawlerController } from './crawler.controller.js';
import { TikTokCrawlerService } from './tiktok-crawler.service.js';
import { YouTubeCrawlerService } from './youtube-crawler.service.js';

@Module({
  imports: [PlatformsModule],
  controllers: [CrawlerController],
  providers: [YouTubeCrawlerService, TikTokCrawlerService, ContentPoolStore],
})
export class CrawlerModule {}
