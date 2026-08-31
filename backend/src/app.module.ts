import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { AlertsModule } from './alerts/alerts.module.js';
import { AppController } from './app.controller.js';
import { AppService } from './app.service.js';
import { CrawlerModule } from './crawler/crawler.module.js';
import { FacebookModule } from './facebook/facebook.module.js';
import { PlatformsModule } from './platforms/platforms.module.js';

@Module({
  imports: [ConfigModule.forRoot({ isGlobal: true }), FacebookModule, AlertsModule, PlatformsModule, CrawlerModule],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
