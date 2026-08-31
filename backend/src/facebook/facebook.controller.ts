import { Controller, Get, Query, Res } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { Response } from 'express';
import { FacebookService } from './facebook.service.js';
import { maskToken } from './mask-token.js';
import { OAuthStateService } from './oauth-state.service.js';
import { PageStoreService, type StoredPage } from './page-store.service.js';

@Controller('facebook')
export class FacebookController {
  private readonly frontendUrl: string;

  constructor(
    private readonly facebook: FacebookService,
    private readonly oauthState: OAuthStateService,
    private readonly pageStore: PageStoreService,
    config: ConfigService,
  ) {
    this.frontendUrl = config.get<string>('FRONTEND_URL', 'http://localhost:5173');
  }

  /** Buoc 1: nguoi dung mo URL nay tren trinh duyet de bat dau OAuth. */
  @Get('login')
  login(@Res() res: Response) {
    const state = this.oauthState.create();
    const url = this.facebook.buildLoginDialogUrl(state);
    return res.redirect(url);
  }

  /** Buoc 2: Facebook redirect ve day sau khi nguoi dung dong y quyen. Luon ket thuc bang redirect ve FE. */
  @Get('callback')
  async callback(
    @Res() res: Response,
    @Query('code') code: string | undefined,
    @Query('state') state: string | undefined,
    @Query('error') error: string | undefined,
    @Query('error_description') errorDescription: string | undefined,
  ) {
    try {
      if (error) {
        return this.redirectWithError(res, `Facebook tu choi: ${error} - ${errorDescription ?? ''}`);
      }
      if (!code) {
        return this.redirectWithError(res, 'Thieu tham so code tu Facebook.');
      }
      if (!this.oauthState.consume(state)) {
        return this.redirectWithError(res, 'State khong hop le hoac da het han, thu dang nhap lai.');
      }

      const shortLived = await this.facebook.exchangeCodeForUserToken(code);
      const longLived = await this.facebook.exchangeForLongLivedToken(shortLived.accessToken);
      const pages = await this.facebook.getManagedPages(longLived.accessToken);

      const toSave: StoredPage[] = pages.map((p) => ({
        pageId: p.id,
        pageName: p.name,
        category: p.category ?? null,
        tasks: p.tasks ?? [],
        pageAccessToken: p.access_token,
        pictureUrl: p.picture?.data?.url ?? null,
        connectedAt: new Date().toISOString(),
      }));
      await this.pageStore.upsertMany(toSave);

      const url = new URL(this.frontendUrl);
      url.searchParams.set('connected', String(toSave.length));
      return res.redirect(url.toString());
    } catch (err: any) {
      return this.redirectWithError(res, err?.message ?? 'Loi khong xac dinh khi ket noi Facebook.');
    }
  }

  private redirectWithError(res: Response, message: string) {
    const url = new URL(this.frontendUrl);
    url.searchParams.set('fb_error', message);
    return res.redirect(url.toString());
  }

  /** Danh sach page da ket noi (che bot access token khi hien thi). */
  @Get('pages')
  async listPages() {
    const pages = await this.pageStore.getAll();
    return pages.map((p) => ({
      pageId: p.pageId,
      pageName: p.pageName,
      category: p.category,
      tasks: p.tasks,
      pictureUrl: p.pictureUrl,
      connectedAt: p.connectedAt,
      pageAccessToken: maskToken(p.pageAccessToken),
    }));
  }
}
