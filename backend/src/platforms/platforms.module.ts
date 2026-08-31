import { Module } from '@nestjs/common';
import { PlatformLoginService } from './platform-login.service.js';
import { PlatformsController } from './platforms.controller.js';
import { SessionStoreService } from './session-store.service.js';

@Module({
  controllers: [PlatformsController],
  providers: [PlatformLoginService, SessionStoreService],
  exports: [SessionStoreService],
})
export class PlatformsModule {}
