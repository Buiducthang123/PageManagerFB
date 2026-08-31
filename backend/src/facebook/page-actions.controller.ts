import { FileInterceptor } from '@nestjs/platform-express';
import {
  BadRequestException,
  Body,
  Controller,
  Delete,
  Get,
  Param,
  Post,
  Put,
  UploadedFile,
  UseInterceptors,
} from '@nestjs/common';
import { diskStorage } from 'multer';
import { randomUUID } from 'node:crypto';
import { stat, unlink, mkdir } from 'node:fs/promises';
import { extname, join } from 'node:path';
import { FacebookService } from './facebook.service.js';
import { maskToken } from './mask-token.js';
import { PageStoreService, type ContentProfile } from './page-store.service.js';

const TMP_DIR = join(process.cwd(), 'data', 'tmp');

@Controller('facebook/pages/:pageId')
export class PageActionsController {
  constructor(
    private readonly facebook: FacebookService,
    private readonly pageStore: PageStoreService,
  ) {}

  /** Thong tin 1 page da ket noi (che bot access token). */
  @Get()
  async getOne(@Param('pageId') pageId: string) {
    const p = await this.pageStore.getById(pageId);
    return {
      pageId: p.pageId,
      pageName: p.pageName,
      category: p.category,
      tasks: p.tasks,
      pictureUrl: p.pictureUrl,
      connectedAt: p.connectedAt,
      pageAccessToken: maskToken(p.pageAccessToken),
      contentProfile: p.contentProfile ?? { keywordGroups: [] },
    };
  }

  /** Nhom tu khoa (theo thu tu uu tien) de Content Engine sau nay biet tim gi cho page nay. */
  @Get('content-profile')
  async getContentProfile(@Param('pageId') pageId: string) {
    const p = await this.pageStore.getById(pageId);
    return p.contentProfile ?? { keywordGroups: [] };
  }

  @Put('content-profile')
  async saveContentProfile(@Param('pageId') pageId: string, @Body() body: ContentProfile) {
    if (!Array.isArray(body?.keywordGroups)) {
      throw new BadRequestException('Thieu keywordGroups (dang mang cac mang tu khoa).');
    }
    const cleaned: string[][] = body.keywordGroups
      .map((group) => (Array.isArray(group) ? group.map((k) => String(k).trim()).filter(Boolean) : []))
      .filter((group) => group.length > 0);
    const updated = await this.pageStore.updateContentProfile(pageId, { keywordGroups: cleaned });
    return updated.contentProfile;
  }

  /** Thong ke co ban cua page: followers + vai bai viet gan nhat. */
  @Get('stats')
  async stats(@Param('pageId') pageId: string) {
    const page = await this.pageStore.getById(pageId);
    const [info, posts] = await Promise.all([
      this.facebook.getPageStats(pageId, page.pageAccessToken),
      this.facebook.getRecentPosts(pageId, page.pageAccessToken, 5),
    ]);

    return {
      followers: info.followers_count ?? info.fan_count ?? null,
      about: info.about ?? null,
      link: info.link ?? null,
      recentPosts: posts.map((p) => ({
        id: p.id,
        message: p.message ?? '',
        createdAt: p.created_time,
        permalinkUrl: p.permalink_url ?? null,
        likeCount: p.likes?.summary?.total_count ?? 0,
        commentCount: p.comments?.summary?.total_count ?? 0,
        shareCount: p.shares?.count ?? 0,
      })),
    };
  }

  /** Test dang 1 bai text len page - de kiem chung quyen pages_manage_posts. */
  @Post('test-post')
  async testPost(@Param('pageId') pageId: string, @Body('message') message: string | undefined) {
    if (!message?.trim()) {
      throw new BadRequestException('Thieu noi dung message.');
    }
    const page = await this.pageStore.getById(pageId);
    const result = await this.facebook.createTestPost(pageId, page.pageAccessToken, message.trim());
    return {
      postId: result.id,
      permalinkUrl: `https://www.facebook.com/${result.id}`,
    };
  }

  /** Xoa 1 bai viet tren page (vd: don bai test). */
  @Delete('posts/:postId')
  async deletePost(@Param('pageId') pageId: string, @Param('postId') postId: string) {
    const page = await this.pageStore.getById(pageId);
    await this.facebook.deletePost(postId, page.pageAccessToken);
    return { deleted: true, postId };
  }

  /** Xoa ket noi Page nay khoi tool (chi xoa trong store cua tool, khong thu hoi quyen tren Facebook). */
  @Delete()
  async disconnect(@Param('pageId') pageId: string) {
    await this.pageStore.remove(pageId);
    return { disconnected: true, pageId };
  }

  /** Test dang Reel: nhan video upload tu FE, chay flow video_reels resumable upload, xoa file tam sau khi xong. */
  @Post('test-reel')
  @UseInterceptors(
    FileInterceptor('video', {
      storage: diskStorage({
        destination: async (_req, _file, cb) => {
          await mkdir(TMP_DIR, { recursive: true });
          cb(null, TMP_DIR);
        },
        filename: (_req, file, cb) => cb(null, `${randomUUID()}${extname(file.originalname)}`),
      }),
    }),
  )
  async testReel(
    @Param('pageId') pageId: string,
    @UploadedFile() file: Express.Multer.File | undefined,
    @Body('description') description: string | undefined,
  ) {
    if (!file) {
      throw new BadRequestException('Thieu file video (field "video").');
    }

    const page = await this.pageStore.getById(pageId);

    try {
      const { size } = await stat(file.path);
      const { video_id, upload_url } = await this.facebook.startReelUpload(pageId, page.pageAccessToken);
      await this.facebook.uploadReelBinary(upload_url, page.pageAccessToken, file.path, size);
      await this.facebook.finishReelUpload(pageId, page.pageAccessToken, video_id, description ?? '');

      return {
        videoId: video_id,
        message: 'Da upload va gui yeu cau publish Reel. Facebook con xu ly (processing) truoc khi hien cong khai.',
        checkStatusUrl: `/facebook/pages/${pageId}/reel-status/${video_id}`,
      };
    } finally {
      await unlink(file.path).catch(() => undefined);
    }
  }

  /** Poll trang thai xu ly cua 1 Reel da upload (processing -> ready), kem thumbnail/link khi da co. */
  @Get('reel-status/:videoId')
  async reelStatus(@Param('pageId') pageId: string, @Param('videoId') videoId: string) {
    const page = await this.pageStore.getById(pageId);
    const res = await this.facebook.getVideoStatus(videoId, page.pageAccessToken);
    return {
      videoId,
      status: res.status ?? null,
      permalinkUrl: res.permalink_url ?? null,
      thumbnailUrl: res.picture ?? null,
      length: res.length ?? null,
    };
  }

  /** Danh sach video/reel da dang tren page. */
  @Get('videos')
  async videos(@Param('pageId') pageId: string) {
    const page = await this.pageStore.getById(pageId);
    const videos = await this.facebook.getPageVideos(pageId, page.pageAccessToken, 12);
    return videos.map((v) => ({
      id: v.id,
      description: v.description ?? '',
      updatedAt: v.updated_time,
      permalinkUrl: v.permalink_url ?? null,
      thumbnailUrl: v.picture ?? null,
      length: v.length ?? null,
    }));
  }
}
