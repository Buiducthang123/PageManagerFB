import { HttpService } from '@nestjs/axios';
import { ConfigService } from '@nestjs/config';
import { BadGatewayException, Injectable, Logger } from '@nestjs/common';
import { readFile } from 'node:fs/promises';
import { firstValueFrom } from 'rxjs';

export const FACEBOOK_LOGIN_SCOPES = [
  'pages_show_list',
  'pages_read_engagement',
  'pages_manage_posts',
  'pages_manage_engagement',
  'pages_manage_metadata',
].join(',');

interface FacebookAccountsResponse {
  data: Array<{
    id: string;
    name: string;
    access_token: string;
    category?: string;
    tasks?: string[];
    picture?: { data?: { url?: string } };
  }>;
  paging?: { next?: string };
}

@Injectable()
export class FacebookService {
  private readonly logger = new Logger(FacebookService.name);
  private readonly graphVersion: string;
  private readonly appId: string;
  private readonly appSecret: string;
  private readonly redirectUri: string;

  constructor(
    private readonly http: HttpService,
    private readonly config: ConfigService,
  ) {
    this.graphVersion = this.config.get<string>('FB_GRAPH_VERSION', 'v21.0');
    this.appId = this.requireEnv('FB_APP_ID');
    this.appSecret = this.requireEnv('FB_APP_SECRET');
    this.redirectUri = this.requireEnv('FB_REDIRECT_URI');
  }

  private requireEnv(key: string): string {
    const value = this.config.get<string>(key);
    if (!value) {
      throw new Error(`Thieu bien moi truong ${key}. Kiem tra file .env (xem .env.example).`);
    }
    return value;
  }

  private graphUrl(path: string): string {
    return `https://graph.facebook.com/${this.graphVersion}${path}`;
  }

  buildLoginDialogUrl(state: string): string {
    const params = new URLSearchParams({
      client_id: this.appId,
      redirect_uri: this.redirectUri,
      state,
      scope: FACEBOOK_LOGIN_SCOPES,
      response_type: 'code',
    });
    return `https://www.facebook.com/${this.graphVersion}/dialog/oauth?${params.toString()}`;
  }

  async exchangeCodeForUserToken(code: string): Promise<{ accessToken: string; expiresIn?: number }> {
    const params = new URLSearchParams({
      client_id: this.appId,
      client_secret: this.appSecret,
      redirect_uri: this.redirectUri,
      code,
    });
    const url = this.graphUrl(`/oauth/access_token?${params.toString()}`);
    const res = await this.get<{ access_token: string; token_type: string; expires_in?: number }>(url);
    return { accessToken: res.access_token, expiresIn: res.expires_in };
  }

  /** Doi short-lived user token (~1-2h) lay long-lived token (~60 ngay). */
  async exchangeForLongLivedToken(shortLivedToken: string): Promise<{ accessToken: string; expiresIn?: number }> {
    const params = new URLSearchParams({
      grant_type: 'fb_exchange_token',
      client_id: this.appId,
      client_secret: this.appSecret,
      fb_exchange_token: shortLivedToken,
    });
    const url = this.graphUrl(`/oauth/access_token?${params.toString()}`);
    const res = await this.get<{ access_token: string; token_type: string; expires_in?: number }>(url);
    return { accessToken: res.access_token, expiresIn: res.expires_in };
  }

  async getManagedPages(userAccessToken: string) {
    const params = new URLSearchParams({
      fields: 'id,name,access_token,category,tasks,picture{url}',
      access_token: userAccessToken,
    });
    const url = this.graphUrl(`/me/accounts?${params.toString()}`);
    const res = await this.get<FacebookAccountsResponse>(url);
    return res.data;
  }

  async getPageStats(pageId: string, pageAccessToken: string) {
    const params = new URLSearchParams({
      fields: 'followers_count,fan_count,about,link',
      access_token: pageAccessToken,
    });
    const url = this.graphUrl(`/${pageId}?${params.toString()}`);
    return this.get<{ followers_count?: number; fan_count?: number; about?: string; link?: string }>(url);
  }

  async getRecentPosts(pageId: string, pageAccessToken: string, limit = 5) {
    const params = new URLSearchParams({
      fields:
        'id,message,created_time,permalink_url,likes.summary(true).limit(0),comments.summary(true).limit(0),shares',
      limit: String(limit),
      access_token: pageAccessToken,
    });
    const url = this.graphUrl(`/${pageId}/posts?${params.toString()}`);
    const res = await this.get<{
      data: Array<{
        id: string;
        message?: string;
        created_time: string;
        permalink_url?: string;
        likes?: { summary?: { total_count?: number } };
        comments?: { summary?: { total_count?: number } };
        shares?: { count?: number };
      }>;
    }>(url);
    return res.data;
  }

  async createTestPost(pageId: string, pageAccessToken: string, message: string) {
    const url = this.graphUrl(`/${pageId}/feed`);
    const body = new URLSearchParams({ message, access_token: pageAccessToken });
    return this.post<{ id: string }>(url, body);
  }

  async deletePost(postId: string, pageAccessToken: string) {
    const url = this.graphUrl(`/${postId}?access_token=${encodeURIComponent(pageAccessToken)}`);
    try {
      const response = await firstValueFrom(this.http.delete(url));
      return response.data as { success?: boolean };
    } catch (err: any) {
      const fbError = err?.response?.data?.error;
      this.logger.error(`Xoa post loi: ${JSON.stringify(fbError ?? err?.message)}`);
      throw new BadGatewayException(fbError?.message ?? 'Xoa bai viet that bai');
    }
  }

  /** video_reels resumable upload - buoc 1: khoi tao, lay video_id + upload_url. */
  async startReelUpload(pageId: string, pageAccessToken: string) {
    const url = this.graphUrl(`/${pageId}/video_reels`);
    const body = new URLSearchParams({ upload_phase: 'start', access_token: pageAccessToken });
    return this.post<{ video_id: string; upload_url: string }>(url, body);
  }

  /** video_reels resumable upload - buoc 2: day bytes video len upload_url (rupload.facebook.com). */
  async uploadReelBinary(uploadUrl: string, pageAccessToken: string, filePath: string, fileSize: number) {
    const buffer = await readFile(filePath);
    try {
      const response = await firstValueFrom(
        this.http.post(uploadUrl, buffer, {
          headers: {
            Authorization: `OAuth ${pageAccessToken}`,
            offset: '0',
            file_size: String(fileSize),
            'Content-Type': 'application/octet-stream',
          },
          maxBodyLength: Infinity,
          maxContentLength: Infinity,
        }),
      );
      return response.data as { success?: boolean };
    } catch (err: any) {
      const fbError = err?.response?.data?.error ?? err?.response?.data;
      this.logger.error(`Reel upload binary loi: ${JSON.stringify(fbError ?? err?.message)}`);
      throw new BadGatewayException(fbError?.message ?? 'Upload video len Facebook that bai');
    }
  }

  /** video_reels resumable upload - buoc 3: bao hoan tat + xin publish. */
  async finishReelUpload(pageId: string, pageAccessToken: string, videoId: string, description: string) {
    const url = this.graphUrl(`/${pageId}/video_reels`);
    const body = new URLSearchParams({
      upload_phase: 'finish',
      video_id: videoId,
      video_state: 'PUBLISHED',
      description,
      access_token: pageAccessToken,
    });
    return this.post<{ success?: boolean }>(url, body);
  }

  async getVideoStatus(videoId: string, pageAccessToken: string) {
    const params = new URLSearchParams({
      fields: 'status,permalink_url,picture,length,description',
      access_token: pageAccessToken,
    });
    const url = this.graphUrl(`/${videoId}?${params.toString()}`);
    return this.get<{
      status?: unknown;
      permalink_url?: string;
      picture?: string;
      length?: number;
      description?: string;
    }>(url);
  }

  /** Danh sach video/reel da dang tren page - GET /{page-id}/videos. */
  async getPageVideos(pageId: string, pageAccessToken: string, limit = 12) {
    const params = new URLSearchParams({
      fields: 'id,description,updated_time,permalink_url,picture,length',
      limit: String(limit),
      access_token: pageAccessToken,
    });
    const url = this.graphUrl(`/${pageId}/videos?${params.toString()}`);
    const res = await this.get<{
      data: Array<{
        id: string;
        description?: string;
        updated_time: string;
        permalink_url?: string;
        picture?: string;
        length?: number;
      }>;
    }>(url);
    return res.data;
  }

  /** Lay hoi thoai Messenger gan nhat cua page, de tim PSID nguoi vua nhan tin (phuc vu bao loi). */
  async getRecentConversation(pageId: string, pageAccessToken: string) {
    const params = new URLSearchParams({
      fields: 'participants,updated_time',
      limit: '1',
      access_token: pageAccessToken,
    });
    const url = this.graphUrl(`/${pageId}/conversations?${params.toString()}`);
    return this.get<{
      data: Array<{
        id: string;
        updated_time: string;
        participants?: { data: Array<{ id: string; name?: string; email?: string }> };
      }>;
    }>(url);
  }

  /** Gui tin nhan Messenger toi 1 PSID - chi thanh cong neu con trong cua so 24h (Facebook messaging policy). */
  async sendMessengerText(pageId: string, pageAccessToken: string, recipientPsid: string, text: string) {
    const url = this.graphUrl(`/${pageId}/messages`);
    const body = new URLSearchParams({
      recipient: JSON.stringify({ id: recipientPsid }),
      message: JSON.stringify({ text }),
      messaging_type: 'RESPONSE',
      access_token: pageAccessToken,
    });
    return this.post<{ recipient_id?: string; message_id?: string }>(url, body);
  }

  private async get<T>(url: string): Promise<T> {
    try {
      const response = await firstValueFrom(this.http.get<T>(url));
      return response.data;
    } catch (err: any) {
      const fbError = err?.response?.data?.error;
      this.logger.error(`Facebook API loi: ${JSON.stringify(fbError ?? err?.message)}`);
      throw new BadGatewayException(fbError?.message ?? 'Facebook API request that bai');
    }
  }

  private async post<T>(url: string, body: URLSearchParams): Promise<T> {
    try {
      const response = await firstValueFrom(this.http.post<T>(url, body));
      return response.data;
    } catch (err: any) {
      const fbError = err?.response?.data?.error;
      this.logger.error(`Facebook API loi: ${JSON.stringify(fbError ?? err?.message)}`);
      throw new BadGatewayException(fbError?.message ?? 'Facebook API request that bai');
    }
  }
}
