import { BadRequestException, Injectable, Logger, NotFoundException } from '@nestjs/common';
import { FacebookService } from '../facebook/facebook.service.js';
import { PageStoreService } from '../facebook/page-store.service.js';
import { AlertConfigStore } from './alert-config.store.js';

@Injectable()
export class AlertsService {
  private readonly logger = new Logger(AlertsService.name);

  constructor(
    private readonly facebook: FacebookService,
    private readonly pageStore: PageStoreService,
    private readonly config: AlertConfigStore,
  ) {}

  async getConfig() {
    return this.config.get();
  }

  /** Tim PSID nguoi vua nhan tin cho page nay, luu lam noi nhan canh bao mac dinh. */
  async setupFromPage(pageId: string) {
    const page = await this.pageStore.getById(pageId);
    const convo = await this.facebook.getRecentConversation(pageId, page.pageAccessToken);
    const latest = convo.data[0];
    if (!latest) {
      throw new NotFoundException('Chua co hoi thoai nao voi page nay. Ban nhan 1 tin bat ky cho page tren Messenger truoc.');
    }
    const participant = latest.participants?.data.find((p) => p.id !== pageId);
    if (!participant) {
      throw new NotFoundException('Khong tim thay nguoi gui trong hoi thoai gan nhat.');
    }

    const cfg = {
      pageId,
      recipientPsid: participant.id,
      recipientName: participant.name ?? null,
      updatedAt: new Date().toISOString(),
    };
    await this.config.save(cfg);
    this.logger.log(`Da luu noi nhan canh bao Messenger: ${participant.name ?? participant.id}`);
    return cfg;
  }

  async sendTest() {
    return this.send('Test canh bao tu PagesManager - neu ban thay tin nay, cau hinh Messenger alert da hoat dong.');
  }

  /** Gui canh bao. Neu chua cau hinh hoac ngoai cua so 24h, khong throw len tren - chi log va tra ve ket qua. */
  async send(text: string): Promise<{ sent: boolean; reason?: string }> {
    const cfg = await this.config.get();
    if (!cfg.pageId || !cfg.recipientPsid) {
      return { sent: false, reason: 'Chua cau hinh nguoi nhan canh bao (goi POST /alerts/setup truoc).' };
    }
    try {
      const page = await this.pageStore.getById(cfg.pageId);
      await this.facebook.sendMessengerText(cfg.pageId, page.pageAccessToken, cfg.recipientPsid, text);
      return { sent: true };
    } catch (err: any) {
      this.logger.warn(`Gui canh bao Messenger that bai: ${err?.message}`);
      return { sent: false, reason: err?.message ?? 'Loi khong xac dinh (co the da ngoai cua so 24h).' };
    }
  }
}
