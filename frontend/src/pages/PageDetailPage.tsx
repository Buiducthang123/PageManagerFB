import {
  ArrowLeft,
  ExternalLink,
  Film,
  Heart,
  Loader2,
  MessageCircle,
  Send,
  Share2,
  Tags,
  Trash2,
  Users,
  Video,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { deletePost, disconnectPage, fetchPage, fetchPageStats, fetchPageVideos } from '../api';
import { ContentProfileModal } from '../components/ContentProfileModal';
import { Modal } from '../components/Modal';
import { StatCard } from '../components/StatCard';
import { TestPostModal } from '../components/TestPostModal';
import { TestReelModal } from '../components/TestReelModal';
import type { ConnectedPage, PageStats, PageVideo, RecentPost } from '../types';
import { formatDuration, toAbsoluteFacebookUrl } from '../utils';

export default function PageDetailPage() {
  const { pageId } = useParams<{ pageId: string }>();
  const navigate = useNavigate();

  const [page, setPage] = useState<ConnectedPage | null>(null);
  const [stats, setStats] = useState<PageStats | null>(null);
  const [videos, setVideos] = useState<PageVideo[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [deletingPostId, setDeletingPostId] = useState<string | null>(null);

  const [showTestPost, setShowTestPost] = useState(false);
  const [showTestReel, setShowTestReel] = useState(false);
  const [showContentProfile, setShowContentProfile] = useState(false);

  async function loadAll(id: string) {
    setLoading(true);
    setLoadError(null);
    try {
      const [p, s, v] = await Promise.all([fetchPage(id), fetchPageStats(id), fetchPageVideos(id)]);
      setPage(p);
      setStats(s);
      setVideos(v);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Lỗi không xác định');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (pageId) loadAll(pageId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pageId]);

  async function handleDisconnect() {
    if (!pageId) return;
    setDisconnecting(true);
    try {
      await disconnectPage(pageId);
      navigate('/');
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Xoá kết nối thất bại');
      setDisconnecting(false);
      setConfirmingDisconnect(false);
    }
  }

  async function handleDeletePost(postId: string) {
    if (!pageId) return;
    setDeletingPostId(postId);
    try {
      await deletePost(pageId, postId);
      setStats((prev) => (prev ? { ...prev, recentPosts: prev.recentPosts.filter((p) => p.id !== postId) } : prev));
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Xoá bài viết thất bại');
    } finally {
      setDeletingPostId(null);
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center py-24 text-slate-500">
        <Loader2 size={20} className="animate-spin" />
      </div>
    );
  }

  if (loadError && !page) {
    return (
      <div className="mx-auto max-w-3xl px-8 py-10">
        <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {loadError}
        </p>
        <Link to="/" className="mt-4 inline-flex items-center gap-1.5 text-sm text-purple-400 hover:text-purple-300">
          <ArrowLeft size={14} /> Quay lại danh sách
        </Link>
      </div>
    );
  }

  if (!page) return null;

  const totalLikes = stats?.recentPosts.reduce((sum, p) => sum + p.likeCount, 0) ?? 0;
  const totalComments = stats?.recentPosts.reduce((sum, p) => sum + p.commentCount, 0) ?? 0;
  const totalShares = stats?.recentPosts.reduce((sum, p) => sum + p.shareCount, 0) ?? 0;

  return (
    <div className="px-8 py-10">
      <Link to="/" className="mb-6 inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-300">
        <ArrowLeft size={14} /> Quay lại danh sách
      </Link>

      {/* Header */}
      <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          {page.pictureUrl ? (
            <img src={page.pictureUrl} alt={page.pageName} className="h-14 w-14 rounded-full border border-purple-500/20 object-cover" />
          ) : (
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-gradient-to-br from-purple-500 to-fuchsia-600 text-lg font-semibold text-white">
              {page.pageName.charAt(0).toUpperCase()}
            </div>
          )}
          <div>
            <h1 className="text-xl font-semibold text-white">{page.pageName}</h1>
            <p className="text-sm text-slate-500">{page.category ?? 'Chưa rõ danh mục'}</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowContentProfile(true)}
            className="flex items-center gap-1.5 rounded-lg border border-purple-500/20 bg-purple-500/5 px-3.5 py-2 text-sm font-medium text-purple-300 transition hover:border-purple-400/40 hover:bg-purple-500/10"
          >
            <Tags size={14} /> Từ khoá
          </button>
          <button
            onClick={() => setShowTestPost(true)}
            className="flex items-center gap-1.5 rounded-lg border border-purple-500/20 bg-purple-500/5 px-3.5 py-2 text-sm font-medium text-purple-300 transition hover:border-purple-400/40 hover:bg-purple-500/10"
          >
            <Send size={14} /> Test đăng bài
          </button>
          <button
            onClick={() => setShowTestReel(true)}
            className="flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-purple-600 to-fuchsia-600 px-3.5 py-2 text-sm font-medium text-white shadow-[0_0_16px_rgba(192,38,211,0.3)] transition hover:from-purple-500 hover:to-fuchsia-500"
          >
            <Video size={14} /> Test đăng Reel
          </button>

          {confirmingDisconnect ? (
            <div className="flex items-center gap-2">
              <button
                onClick={handleDisconnect}
                disabled={disconnecting}
                className="rounded-lg bg-red-600 px-3 py-2 text-xs font-medium text-white hover:bg-red-500 disabled:opacity-50"
              >
                {disconnecting ? <Loader2 size={13} className="animate-spin" /> : 'Xác nhận xoá'}
              </button>
              <button
                onClick={() => setConfirmingDisconnect(false)}
                className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-white/5"
              >
                Huỷ
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirmingDisconnect(true)}
              className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/5 px-3.5 py-2 text-sm font-medium text-red-300 transition hover:border-red-400/50 hover:bg-red-500/10"
            >
              <Trash2 size={14} />
              Xoá kết nối
            </button>
          )}
        </div>
      </div>

      {loadError && (
        <div className="mb-6 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {loadError}
        </div>
      )}

      {/* Thong ke tong quan */}
      <div className="mb-10 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="Người theo dõi" value={String(stats?.followers ?? '-')} icon={Users} tone="indigo" />
        <StatCard label="Lượt thích (5 bài)" value={String(totalLikes)} icon={Heart} tone="amber" />
        <StatCard label="Bình luận (5 bài)" value={String(totalComments)} icon={MessageCircle} tone="emerald" />
        <StatCard label="Chia sẻ (5 bài)" value={String(totalShares)} icon={Share2} tone="indigo" />
      </div>

      <div className="grid grid-cols-1 gap-10 xl:grid-cols-2">
        {/* Bai viet gan day */}
        <section>
          <h2 className="mb-3 text-sm font-medium text-slate-300">Bài viết gần đây</h2>
          {!stats || stats.recentPosts.length === 0 ? (
            <p className="rounded-xl border border-dashed border-slate-700 bg-slate-900/30 px-4 py-6 text-center text-sm text-slate-500">
              Chưa có bài viết nào.
            </p>
          ) : (
            <div className="space-y-3">
              {stats.recentPosts.map((post) => (
                <PostRow key={post.id} post={post} onDelete={handleDeletePost} deleting={deletingPostId === post.id} />
              ))}
            </div>
          )}
        </section>

        {/* Reels & Videos */}
        <section>
          <h2 className="mb-3 text-sm font-medium text-slate-300">Reels &amp; Video đã đăng</h2>
          {videos.length === 0 ? (
            <p className="rounded-xl border border-dashed border-slate-700 bg-slate-900/30 px-4 py-6 text-center text-sm text-slate-500">
              Chưa có video/reel nào.
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {videos.map((v) => (
                <VideoTile key={v.id} video={v} />
              ))}
            </div>
          )}
        </section>
      </div>

      {showTestPost && (
        <TestPostModal
          pageId={page.pageId}
          onClose={() => setShowTestPost(false)}
          onPosted={() => pageId && loadAll(pageId)}
        />
      )}
      {showTestReel && (
        <TestReelModal
          pageId={page.pageId}
          onClose={() => setShowTestReel(false)}
          onPublished={() => pageId && loadAll(pageId)}
        />
      )}
      {showContentProfile && (
        <ContentProfileModal pageId={page.pageId} onClose={() => setShowContentProfile(false)} />
      )}
    </div>
  );
}

function VideoTile({ video }: { video: PageVideo }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="neon-border neon-border-hover group relative aspect-[9/16] overflow-hidden rounded-xl bg-slate-900/50 text-left"
      >
        {video.thumbnailUrl ? (
          <img src={video.thumbnailUrl} alt={video.description} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-slate-700">
            <Film size={24} />
          </div>
        )}
        <div className="absolute inset-0 flex items-end bg-gradient-to-t from-black/80 via-black/0 to-black/0 p-2 opacity-0 transition group-hover:opacity-100">
          <p className="line-clamp-3 text-[11px] text-slate-200">{video.description || '(không có mô tả)'}</p>
        </div>
        {video.length !== null && (
          <span className="absolute right-1.5 top-1.5 rounded bg-black/70 px-1.5 py-0.5 text-[10px] text-slate-200">
            {formatDuration(video.length)}
          </span>
        )}
      </button>

      {open && (
        <Modal title="Chi tiết video" onClose={() => setOpen(false)}>
          {video.thumbnailUrl && (
            <img src={video.thumbnailUrl} alt={video.description} className="neon-border mb-3 w-full rounded-lg object-cover" />
          )}
          <p className="mb-2 text-sm text-slate-300">{video.description || '(không có mô tả)'}</p>
          <p className="mb-3 text-xs text-slate-500">
            {new Date(video.updatedAt).toLocaleString('vi-VN')}
            {video.length !== null && ` · ${formatDuration(video.length)}`}
          </p>
          {video.permalinkUrl && (
            <a
              href={toAbsoluteFacebookUrl(video.permalinkUrl)}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1 text-sm text-purple-400 hover:text-purple-300"
            >
              Xem trên Facebook <ExternalLink size={13} />
            </a>
          )}
        </Modal>
      )}
    </>
  );
}

function PostRow({
  post,
  onDelete,
  deleting,
}: {
  post: RecentPost;
  onDelete: (id: string) => void;
  deleting: boolean;
}) {
  const [confirming, setConfirming] = useState(false);

  return (
    <div className="neon-border rounded-xl bg-slate-900/40 p-4">
      <p className="mb-2 text-sm text-slate-200">{post.message || '(không có nội dung text)'}</p>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
        <div className="flex flex-wrap items-center gap-3">
          <span>{new Date(post.createdAt).toLocaleString('vi-VN')}</span>
          <span className="flex items-center gap-1">
            <Heart size={12} /> {post.likeCount}
          </span>
          <span className="flex items-center gap-1">
            <MessageCircle size={12} /> {post.commentCount}
          </span>
          <span className="flex items-center gap-1">
            <Share2 size={12} /> {post.shareCount}
          </span>
          {post.permalinkUrl && (
            <a
              href={toAbsoluteFacebookUrl(post.permalinkUrl)}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1 text-purple-400 hover:text-purple-300"
            >
              Xem <ExternalLink size={11} />
            </a>
          )}
        </div>

        {confirming ? (
          <div className="flex items-center gap-2">
            <span>Xoá bài này?</span>
            <button
              onClick={() => onDelete(post.id)}
              disabled={deleting}
              className="rounded bg-red-600 px-2 py-1 text-white hover:bg-red-500 disabled:opacity-50"
            >
              {deleting ? <Loader2 size={11} className="animate-spin" /> : 'Xoá'}
            </button>
            <button onClick={() => setConfirming(false)} className="rounded border border-slate-700 px-2 py-1 hover:bg-white/5">
              Huỷ
            </button>
          </div>
        ) : (
          <button
            onClick={() => setConfirming(true)}
            className="flex items-center gap-1 rounded px-2 py-1 text-red-400 hover:bg-red-500/10 hover:text-red-300"
          >
            <Trash2 size={12} /> Xoá
          </button>
        )}
      </div>
    </div>
  );
}
