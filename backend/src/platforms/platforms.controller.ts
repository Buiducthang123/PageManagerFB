import { Controller, Get, Param, Post } from '@nestjs/common';
import { PlatformLoginService } from './platform-login.service.js';
import { SessionStoreService } from './session-store.service.js';

const SUPPORTED_PLATFORMS = ['tiktok'];

@Controller('platforms')
export class PlatformsController {
  constructor(
    private readonly login: PlatformLoginService,
    private readonly sessionStore: SessionStoreService,
  ) {}

  @Get()
  async list() {
    return Promise.all(
      SUPPORTED_PLATFORMS.map(async (platform) => {
        const meta = await this.sessionStore.getMeta(platform);
        return {
          platform,
          loggedIn: !!meta,
          loggedInAt: meta?.loggedInAt ?? null,
          loginWindowOpen: this.login.isPending(platform),
        };
      }),
    );
  }

  @Post(':platform/login/start')
  start(@Param('platform') platform: string) {
    return this.login.startLogin(platform);
  }

  @Post(':platform/login/finish')
  finish(@Param('platform') platform: string) {
    return this.login.finishLogin(platform);
  }

  @Post(':platform/login/cancel')
  cancel(@Param('platform') platform: string) {
    return this.login.cancelLogin(platform);
  }
}
