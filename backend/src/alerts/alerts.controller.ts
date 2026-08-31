import { Body, Controller, Get, Post } from '@nestjs/common';
import { AlertsService } from './alerts.service.js';

@Controller('alerts/messenger')
export class AlertsController {
  constructor(private readonly alerts: AlertsService) {}

  @Get('config')
  getConfig() {
    return this.alerts.getConfig();
  }

  /** Body: { pageId } - tim PSID nguoi vua nhan tin cho page do, luu lam noi nhan canh bao. */
  @Post('setup')
  setup(@Body('pageId') pageId: string) {
    return this.alerts.setupFromPage(pageId);
  }

  @Post('test')
  test() {
    return this.alerts.sendTest();
  }
}
