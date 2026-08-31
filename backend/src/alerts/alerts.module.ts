import { Module } from '@nestjs/common';
import { FacebookModule } from '../facebook/facebook.module.js';
import { AlertConfigStore } from './alert-config.store.js';
import { AlertsController } from './alerts.controller.js';
import { AlertsService } from './alerts.service.js';

@Module({
  imports: [FacebookModule],
  controllers: [AlertsController],
  providers: [AlertsService, AlertConfigStore],
  exports: [AlertsService],
})
export class AlertsModule {}
