import { HttpModule } from '@nestjs/axios';
import { Module } from '@nestjs/common';
import { FacebookController } from './facebook.controller.js';
import { FacebookService } from './facebook.service.js';
import { OAuthStateService } from './oauth-state.service.js';
import { PageActionsController } from './page-actions.controller.js';
import { PageStoreService } from './page-store.service.js';

@Module({
  imports: [HttpModule],
  controllers: [FacebookController, PageActionsController],
  providers: [FacebookService, OAuthStateService, PageStoreService],
  exports: [FacebookService, PageStoreService],
})
export class FacebookModule {}
