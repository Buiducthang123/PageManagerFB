# "Dự án tự động" — crawl Douyin → dịch/lồng giọng tự động → đăng TikTok/Facebook

> File plan để xác nhận trước khi code. Không phải tài liệu vận hành — sau khi
> duyệt và làm xong từng phase có thể xoá hoặc cập nhật lại theo thực tế.

## Trạng thái

**Phase 1 ĐÃ XONG và đã test thật end-to-end** (không phải chỉ compile được):
tạo dự án tự động thật (`app/social.py`, model trong `app/models.py`, route
trong `app/main.py`, trang `AutomatedPage.tsx`/`AutomatedDetailPage.tsx`) →
Crawl 1 trang Douyin thật ra 213 video, không trùng khi crawl lại lần 2 → kích
hoạt 1 video (31.4s) → tự chạy hết ingest→transcribe→translate→tts→assemble→
export KHÔNG CẦN bấm tay từng bước (nhờ `_maybe_chain` có sẵn + hook mới
`_maybe_chain_social`) → video xuất ra thật (~20MB), hàng đợi tự chuyển
`ready`. Giao diện đã chụp màn hình xác nhận hiển thị đúng.

**Phase 3 (Playwright đăng bài) đã dựng khung xong**, tham khảo trực tiếp
`Katzca/AutoSocial/src/tiktok-uploader.js` (persistent context, tìm nút
Publish bằng heuristic, chờ xác nhận qua network response + text cue):
`app/stages/social_publish.py` (`login_tiktok_interactive`, `publish_tiktok`),
route đăng nhập/đăng bài trong `app/main.py`, UI trong
`AutomatedDetailPage.tsx`. Đã test thật cơ chế mở/đóng/huỷ cửa sổ Chrome đăng
nhập (không phải chỉ compile) — CHƯA test được bước đăng bài thật vì cần tài
khoản TikTok thật của người dùng đăng nhập tay. Phát hiện quan trọng trong
lúc test: **không có cách nào đáng tin cậy để phát hiện "đã đăng nhập thật
hay chưa" từ bên ngoài** (đã thử tên cookie, nội dung trang, biến JS — TikTok
dùng chung cookie cho khách vãng lai lẫn tài khoản thật) — badge trạng thái
trên UI chỉ là gợi ý yếu ("đã setup" hay chưa), không đảm bảo đăng nhập còn
hợp lệ.

Phase 2 (lập lịch tự động — tick nền, tự crawl/kích hoạt/đăng theo giờ) CHƯA
làm — làm tiếp khi được yêu cầu. Hiện tại crawl/kích hoạt/đăng đều bấm tay
qua UI.

**Cập nhật quan trọng sau khi test thật với account TikTok thật của người
dùng**: bước gán file video vào ô upload (`_set_video_file`) đã đổi HẲN cách
làm, sau khi tìm ra 2 lớp bug thật liên tiếp (không phải đoán):
1. Bug 1 (đã sửa): so khớp nút "Select video" theo kiểu chuỗi con bắt nhầm 1
   khối lớn khác trên trang có chữ đó lẫn bên trong — đổi sang so khớp chính
   xác cả chuỗi.
2. Phát hiện sâu hơn (quan trọng): TikTok Studio hiện KHÔNG còn đọc file
   qua `<input type="file">.files` nữa dù dùng đúng API `set_input_files`
   hay `expect_file_chooser` chuẩn của Playwright — cả 2 đều "thành công"
   không báo lỗi nhưng `files.length` luôn = 0. Chỉ có cách DUY NHẤT ăn: giả
   lập sự kiện `drop` thật với `DataTransfer` chứa `File` dựng trong trình
   duyệt (encode file ra base64 rồi decode lại trong JS) — đúng như UI có
   ghi "Or drag and drop it here". Đã xác nhận qua ảnh/log thật (thấy đúng
   "Duration: 0m32s", % tiến độ upload, Cover/Hashtags/Location) — không
   phải đoán.

Đã test qua `_set_video_file` thật (đưa file vào, thấy màn chỉnh sửa hiện
lên đúng) — CHƯA tự bấm Publish thay người dùng (cố tình dừng lại đúng chỗ
đó, để người dùng tự bấm "Đăng lại" xác nhận bước cuối, không tự đăng bài
thay).

**Bug 3 (đã sửa) — báo "đã đăng" SAI dù chưa hề đăng thật**: người dùng tự
bấm đăng thật, hệ thống báo `posted` nhưng video KHÔNG hề lên TikTok. Nguyên
nhân xác nhận qua ảnh chụp lúc "thành công": video vẫn còn ở màn soạn thảo,
nút Post còn nguyên chưa bấm được. Do 2 lỗi cộng dồn:
1. `_click_publish` dùng so khớp chuỗi con nhãn "post" — khớp NHẦM vào mục
   điều hướng sidebar "Posts" (quản lý bài đã đăng), bấm vào đó đổi URL
   trang thay vì bấm nút Post thật.
2. `_wait_for_publish_confirmation` coi việc ĐỔI URL là dấu hiệu đủ để báo
   thành công — khớp đúng với hệ quả của lỗi 1, tạo thành 1 vòng lặp báo sai
   hoàn hảo. Đồng thời pattern URL mạng theo dõi quá rộng
   ("/creator","/studio","/aweme","/upload") khớp trúng API nền SPA gọi liên
   tục không liên quan gì publish.

Đã sửa: `_click_publish` chỉ còn dùng đúng 1 chiến lược (JS evaluate có loại
trừ hẳn nav/sidebar + chỉ nhận "post" khi khớp CHÍNH XÁC văn bản nút, không
khớp chuỗi con) — bỏ hẳn bước `get_by_role` chuỗi con nguy hiểm. Thu hẹp
pattern URL mạng chỉ còn đúng `/publish`. Bỏ "success" khỏi danh sách cụm từ
xác nhận (quá chung chung). Thêm log console/screenshot ngay cả lúc báo
THÀNH CÔNG (trước chỉ log lúc lỗi) — có bằng chứng đối chiếu lần sau, không
phải tin mù.

**Bug 4 (đã sửa) — tự bấm nhầm "Cancel" của thanh upload đang tải dở**:
`_dismiss_overlays` (chạy trước mỗi bước, dùng để tự đóng popup gợi ý) tìm
nút theo TOÀN TRANG, không giới hạn — bắt trúng nút "Cancel" THẬT cạnh thanh
tiến độ upload (99%, "0 seconds left"), hiện hộp thoại "Sure you want to
cancel your upload?" — suýt huỷ mất video đang tải dở. Đã sửa: giới hạn tìm
nút CHỈ trong các khối dialog/modal/popover thật (`[role='dialog']`,
`[aria-modal='true']`...), không còn quét toàn trang. Thêm bước chờ thanh
upload biến mất hẳn trước khi bấm Publish (an toàn hơn, tránh thao tác giữa
lúc còn đang tải).

**Bug 5 (đã sửa) — không chờ "Content check lite" xong**: TikTok tự kiểm tra
video có vi phạm Community Guidelines không TRƯỚC khi cho đăng — đã xác nhận
thật (đọc trực tiếp `button.is_disabled()`): nút Post bị **khoá cứng** suốt
lúc kiểm tra (TikTok ghi "khoảng 10 phút, video dài hơn có thể lâu hơn"),
không phải bước có thể bỏ qua bằng automation. Thêm `_wait_for_content_check`
— chờ tới khi nút Post thật sự bật lại (tối đa 12 phút) trước khi thử bấm,
thay vì cố bấm 1 nút đang khoá rồi retry vô ích.

**Lưu ý vận hành quan trọng**: 1 lượt đăng giờ có thể mất tới ~12-15 phút
(chủ yếu chờ content check) — cần tính vào lịch đăng ở Phase 2 sau này (N
video/ngày phải chừa đủ khoảng cách, không đăng dồn dập).

**Bug 6 (đã sửa, rút kinh nghiệm từ Bug 4)**: sau khi bỏ "cancel" khỏi
`_DISMISS_LABELS`, có thử THÊM 1 lớp phòng vệ nữa — giới hạn `_dismiss_overlays`
chỉ quét trong khối `[class*='modal']`/`[role='dialog']`... — nhưng lại BỎ SÓT
popup gợi ý thật ("Preview your video on your phone / Got it") vì nó không
nằm trong khối có class chuẩn đó, khiến quá trình bị kẹt lại chờ người dùng
tự bấm tay. Đã bỏ giới hạn phạm vi này — quay lại quét toàn trang như ban
đầu (an toàn vì đã bỏ đúng từ nguy hiểm "cancel" ở Bug 4, không cần giới hạn
phạm vi thêm nữa).

**Bug 7 (đã sửa) — chưa biết xử lý hộp thoại "Continue to post?"**: nút Post
chỉ bị khoá CỨNG một lúc NGẮN ngay sau upload (không phải suốt ~10 phút của
"Content check lite" như hiểu nhầm ở Bug 5) — bấm được sớm hơn nhiều, nhưng
nếu bấm trong lúc content check (khác music copyright check) CHƯA xong,
TikTok hỏi lại qua hộp thoại riêng "Continue to post? ... Do you want to
continue posting before the check is complete?" (2 nút Cancel/Post now) —
hộp thoại này KHÔNG khớp bất kỳ nhãn dismiss/publish/confirm nào đã có, khiến
code đứng im chờ tới timeout. Thêm hàm riêng `_confirm_post_now` — nhận diện
đúng hộp thoại này, bấm "Post now" (khớp đúng ý người dùng: chỉ cần đợi Music
copyright check xong là đủ, không cần chờ hết Content check lite). Rút ngắn
`_wait_for_content_check` từ 12 phút xuống 90s (không cần chờ lâu như tưởng).

## Bối cảnh (đã sửa lại theo đúng ý người dùng — bản trước hiểu sai)

**Bước lấy thông tin video từ Douyin (crawl) đã có sẵn, KHÔNG phải việc mới**:
người dùng đã có tính năng lấy toàn bộ thông tin video của 1 trang cá nhân
Douyin, lưu ra JSON (chính là `scan_profile_info`/mode "info" trong
`app/stages/douyin_dl.py` + trang `/download`, đã làm ở phần trước của
session). "Dự án tự động" KHÔNG cần tự xây lại bước này — chỉ cần TÁI DÙNG kết
quả đó làm nguồn cho hàng đợi.

**Luồng vận hành mỗi ngày** (đúng theo mô tả người dùng):

1. Tính năng tự động khởi động (theo lịch, mỗi ngày).
2. Lần lượt đi qua từng "dự án" (= mỗi cặp Douyin nguồn ↔ đích) đã bật lịch chạy.
3. Với mỗi dự án: lấy **video cũ nhất CHƯA ĐĂNG** trong hàng đợi — ưu tiên
   dùng **"video của tôi"** (video ĐÃ QUA pipeline dịch/lồng giọng/xuất của
   chính hệ thống này, không phải video gốc Douyin) — nếu video đó đã sẵn sàng
   (`ready`) thì đăng; nếu chưa, cần chạy pipeline cho nó trước.
4. Đăng đủ **N video/ngày** theo cấu hình của dự án đó rồi chuyển sang dự án kế tiếp.
5. **Đã xác nhận: vẫn build Phase 3 (Playwright) đầy đủ** — câu "quản lý api
   rồi" chỉ là bối cảnh (đã từng thử/định hướng API trước đó trong buổi làm
   việc này), không phải yêu cầu bỏ bớt việc. Đăng bài thật vẫn do hệ thống
   này tự làm, qua Playwright như đã chốt.

**Cấu hình chung + cấu hình riêng**: mỗi "dự án" (cặp nguồn/đích) cần 2 tầng
cấu hình — 1 bộ mặc định DÙNG CHUNG cho mọi dự án (vd engine dịch, giọng đọc
mặc định, số video/ngày mặc định...), và mỗi dự án có thể TỰ GHI ĐÈ riêng lẻ
nếu cần khác biệt (vd dự án A muốn giọng khác, đăng nhiều bài/ngày hơn dự án B).

Phạm vi thí điểm ban đầu: ~20 cặp nguồn/đích. Hướng đăng bài (nếu build) đã
chốt: **Playwright — mỗi tài khoản TikTok 1 browser session/context riêng**
(tham khảo cách làm của `wkaisertexas/tiktok-uploader`, viết lại theo pattern
resilience đã dùng cho `douyin_dl.py`, không cài nguyên repo làm dependency).
Đánh đổi: không bị giới hạn 5 acc/24h của API chưa-audit, nhưng dễ gãy khi
TikTok đổi giao diện, rủi ro bị phát hiện automation cao hơn — người dùng đã
cân nhắc và chọn hướng này.

Vì là tính năng lớn, chia làm 3 phase. **Chỉ Phase 1 làm ngay** sau khi bạn
duyệt file này — Phase 2/3 mô tả kiến trúc trước, code viết khi tới lượt.

## Quy ước tái dùng từ code hiện có

- **Lưu trữ**: không dùng database — mọi thứ là JSON file/thư mục +
  `threading.Lock`, giống hệt cách `app/projects.py` đang lưu project hiện tại
  (mỗi project 1 thư mục, `project.json` + khoá riêng, cộng 1 `index.json`
  dùng chung cho danh sách). Module mới nhái đúng pattern này.
- **Job chạy nền**: dùng lại nguyên `app/jobs.py` (`start_job`,
  `is_job_running`, `request_cancel`, polling status qua HTTP) cho mỗi lượt
  crawl/kích hoạt.
- **Auto-chain pipeline**: `_maybe_chain` trong `app/main.py` hiện tự chạy
  ingest→transcribe→translate→tts→assemble rồi dừng (export để tay). Thêm 1
  hook riêng `_maybe_chain_social` — chỉ áp dụng cho project sinh ra từ hàng
  đợi tự động, không đổi hành vi mặc định cho project tạo tay.

## Việc sửa nhỏ trên code cũ (làm trước, đã xong)

- `app/stages/douyin_dl.py::read_video_info` — thêm field `aweme_id` (trước
  đây không lấy field này, cần để crawl lại biết video nào đã biết, tránh xử
  lý trùng).

## Phase 1 — Data model + CRUD + crawl-vào-hàng-đợi + liên kết pipeline

### Backend

**`app/social.py`** (mới) — mirror `app/projects.py`:
- `SOCIAL_DIR`, `INDEX_PATH`, `social_dir(id)`, `state_path(id)`
- `load_state(id)`, `save_state(state)`, `locked_state(id)` (contextmanager tự load→yield→save)
- `list_social_projects()`, `create_social_project(...)`, `delete_social_project(id)`

**`app/models.py`** (đã thêm, cần sửa lại field đăng bài cho khớp Playwright,
và thêm tầng cấu hình chung) —
`QueueItemStatus`, `QueueItem`, `SocialProjectState`, `SocialProjectSummary`, và
field `ProjectState.social_link: Optional[SocialLink]` (đánh dấu project nào do
hàng đợi tự động sinh ra). `SocialProjectState` đổi field đăng bài từ
token/OAuth sang: `tiktok_session_path: str` (đường dẫn file `storage_state`
Playwright của account đó, rỗng = chưa đăng nhập).

**Cấu hình chung + riêng** — thêm `SocialGlobalSettings` (1 file JSON dùng
chung, `workspace/social/settings.json`): `engine`, `tts_engine`, `voice`,
`audio_mode`, `min_video_speed`, `posts_per_day` mặc định. Mỗi
`SocialProjectState` đổi các field cấu hình cùng tên sang `Optional[...] = None`
— `None` nghĩa là "dùng theo cấu hình chung", có giá trị nghĩa là "dự án này tự
ghi đè riêng". Hàm `effective_config(state, global_settings)` gộp 2 tầng lại
thành 1 bộ giá trị cuối cùng dùng khi crawl/kích hoạt/đăng.

**`app/schemas.py`** — `CreateSocialProjectRequest`, `UpdateSocialProjectRequest`.

**`app/main.py`** — route mới:
- `GET /api/social/settings`, `PATCH /api/social/settings` — đọc/sửa cấu hình chung
- `POST /api/social` — tạo cặp nguồn/đích
- `GET /api/social` — danh sách
- `GET /api/social/{id}` — chi tiết + hàng đợi
- `PATCH /api/social/{id}` — sửa cấu hình / pause-resume
- `DELETE /api/social/{id}`
- `POST /api/social/{id}/crawl` — job quét video mới (so `aweme_id`, chỉ thêm mục mới)
- `POST /api/social/{id}/queue/{aweme_id}/activate` — tạo 1 project thật, ingest
  từ `play_url`, bật `auto_pipeline`, chạy tới hết export
- Hook `_maybe_chain_social(project_id, finished_stage)` — sau `assemble` tự
  kích `export` luôn (không chờ người duyệt CapCut); sau `export` xong, đánh
  dấu `queue_item.status = "ready"` trong đúng hàng đợi social tương ứng.

### Frontend

- `frontend/src/lib/api.ts` — thêm type + hàm gọi API tương ứng.
- Trang mới `AutomatedPage.tsx` (danh sách cặp nguồn/đích) + `AutomatedDetailPage.tsx`
  (form cấu hình, nút "Crawl ngay", bảng hàng đợi, nút "Kích hoạt").
- Route `/automated`, `/automated/:id` + mục nav trong sidebar.

**Phase 1 xong nghĩa là**: tạo được cặp nguồn/đích → bấm Crawl → hàng đợi có
video mới, không trùng → bấm Kích hoạt 1 video → tự chạy hết dịch/lồng
giọng/xuất, chuyển `ready` khi xong. **Chưa có lịch tự động, chưa đăng bài
thật** — 2 việc đó làm tay qua nút bấm ở Phase 1.

## Phase 2 — Bộ lập lịch (sau khi Phase 1 chạy ổn)

1 thread nền khởi động cùng FastAPI, tick mỗi ~60s, quét mọi cặp `active`:
- Tới giờ crawl lại (mặc định 24h, cấu hình được) → tự crawl, không cần bấm tay.
- Có video `pending` cũ nhất và chưa có video nào đang `processing` cho cặp đó
  → tự kích hoạt.
- Có video `ready` và tới khung giờ đăng theo `posts_per_day` (chia đều trong
  ngày) → gọi bước đăng (Phase 3).

## Phase 3 — Publisher (đăng thật, Playwright)

**Nguồn tham khảo đã xác nhận: [`Katzca/AutoSocial`](https://github.com/Katzca/AutoSocial)**
(1263 sao, cập nhật 06/2026, có CI, Windows là target chính) — KHÔNG phải
PostFlow/tiktok-uploader như đề xuất ban đầu (đã kiểm tra kỹ: repo fork
`toannguyen1412/auto-social-post` từng được đề xuất trước đó thực ra là fork cá
nhân 0 sao của chính repo này, không có thay đổi gì). `AutoSocial` là Node.js
nên KHÔNG chạy song song làm sidecar — chỉ đọc `src/tiktok-uploader.js` (~39KB,
code thật) để lấy đúng luồng thao tác Playwright (selector, thứ tự upload →
caption → cover → publish, cách retry/xử lý lỗi), viết lại bằng Python
Playwright vào `app/stages/social_publish.py`. Kiến trúc `account-manager.js`/
`queue.js`/`scheduler.js` của repo cũng khớp gần như y hệt Phase 1/2 đã thiết
kế — dùng để đối chiếu, không copy trực tiếp (khác ngôn ngữ).

**Thí điểm trước với 1 tài khoản TikTok** (người dùng đã chốt) — Phase 3 khi
build xong chỉ test/dùng thật với 1 account trước, chưa mở rộng ra cả 20.

Bọc bằng đúng pattern resilience `douyin_dl.py` đang dùng (retry, timeout,
log chi tiết từng bước, chụp screenshot khi lỗi để biết chính xác TikTok đổi
UI ở đâu).

- **Đăng nhập 1 lần/account**: route mới `POST /api/social/{id}/tiktok/login`
  — mở 1 cửa sổ Chrome THẬT (`headless=False`, Playwright) tới trang đăng nhập
  TikTok, đợi người dùng tự đăng nhập tay (kể cả 2FA/captcha nếu có), sau đó
  lưu `context.storage_state()` ra file `workspace/social/<id>/tiktok_state.json`,
  cập nhật `state.tiktok_session_path`. Làm 1 lần/account, không phải mỗi lần đăng.
- `publish_tiktok(video_path, caption, session_path) -> None` — mở context mới
  với `storage_state=session_path` (khôi phục đăng nhập, không cần login lại),
  vào TikTok Studio upload, thao tác theo đúng luồng tham khảo từ repo, đăng
  xong lưu lại `storage_state` mới (cookie có thể refresh sau mỗi lần dùng).
- `publish_facebook(...)` — giữ nguyên Graph API (Facebook không nằm trong
  quyết định đổi hướng lần này, ưu tiên TikTok trước, Facebook để sau/optional
  như đã chốt trước đó).
- **Bỏ hẳn** phần OAuth callback route cho TikTok — không cần domain HTTPS/redirect
  URI nữa, việc đăng ký app TikTok Developer trước đó không còn cần thiết cho
  hướng Playwright (chỉ cần nếu sau này đổi lại sang API).

## Cập nhật mặc định xuất video (2026-09-23)

Sau khi xác nhận 1 video đã đăng thật lên TikTok, phát hiện video xuất ra
KHÔNG che phụ đề cũ — do `export_blur_region` chỉ được set tay qua UI
(`OcrCropSelector`), mà dự án tự động không có ai ngồi thao tác UI nên field
này luôn `None`. Người dùng chốt 3 thay đổi mặc định, áp dụng cho TOÀN BỘ
tính năng (project thường, dự án dài tập/split, VÀ dự án tự động):

1. **Tự động dò + che phụ đề cũ bằng OCR làm mặc định** khi chưa khoanh vùng
   tay (`_start_export` trong `app/main.py` giờ tự gọi
   `ocr_stage.detect_subtitle_region()` khi `state.export_blur_region` rỗng,
   rồi LƯU LẠI kết quả vào `export_blur_region` để không phải dò lại mỗi lần
   xuất và để UI vẫn hiển thị đúng vùng đã che).
2. **Đổi mặc định `audio_mode` từ "separated" sang "original"** (giữ nguyên
   âm thanh gốc) ở mọi nơi: `app/schemas.py` (3 chỗ), `app/models.py`
   (`ProjectState.auto_audio_mode`, `SocialProjectState.audio_mode`), và mọi
   dropdown mặc định ở frontend (`DirectExportPanel.tsx`, `ProjectDetail.tsx`
   x2 chỗ — assemble CapCut đơn + auto-pipeline).
3. **Thêm field mới `original_audio_volume_db` (mặc định -13dB)** — chỉ có
   tác dụng khi `audio_mode="original"`. Threading qua cả 2 đường dựng video:
   - `export_direct.py::render_video` — tham số mới `background_volume_db`,
     override hằng số tuyến tính cũ `BACKGROUND_VOLUME=0.8` bằng
     `volume={db}dB` khi có giá trị.
   - `assemble.py::assemble_project`/`assemble_multi` (đường CapCut) — tham
     số mới `background_volume` (tuyến tính, CapCut draft không hiểu dB) —
     `app/main.py::_resolve_background_volume_linear()` tự quy đổi
     `10**(db/20)` khi `audio_mode="original"`, giữ nguyên 0.8 cho mode khác.
   Field mới có mặt ở: `StartAssembleRequest`, `StartExportRequest`,
   `UpdateAutoPipelineRequest`, `UpdateSocialProjectRequest` (schemas.py),
   `ProjectState.auto_original_audio_volume_db`,
   `SocialProjectState.original_audio_volume_db` (models.py) — và UI nhập dB
   mới xuất hiện cạnh dropdown "Âm thanh gốc" ở cả `DirectExportPanel.tsx`
   lẫn `ProjectDetail.tsx` (chỉ hiện khi chọn "Giữ nguyên âm thanh gốc").

Đã xác nhận: `python -m py_compile` sạch cho mọi file backend đã sửa,
`npx tsc -b --noEmit` sạch cho frontend. CHƯA test thật bằng cách xuất 1 video
mới (khuyến nghị làm trước khi kích hoạt video tiếp theo trong dự án tự động,
để xác nhận vùng che OCR + mức âm lượng -13dB nghe hợp lý trên video thật).

## Dịch tiêu đề Trung → Việt cho dự án tự động (2026-09-23)

Người dùng hỏi: title Douyin (tiếng Trung nguyên mẫu) nên được dịch sang Việt
chứ? → Đúng, trước đó `QueueItem.title` giữ nguyên bản Trung suốt — dùng làm
cả tên dự án lẫn caption đăng TikTok, chưa hề dịch.

Thêm `QueueItem.title_vi: Optional[str]` — dịch LƯỜI (chỉ lúc kích hoạt 1
video cụ thể trong `activate_queue_item_route`, KHÔNG dịch hết cả hàng đợi
lúc crawl) qua hàm mới `translate.translate_title()` (gọi thẳng Gemini,
schema `{"vi": str}`, không dùng lại `process_llm` vì đó là dịch theo batch
nhiều cue, không hợp cho 1 câu ngắn lẻ). Lý do dịch lười: Gemini Free Tier
giới hạn 20 request/ngày (xem `translate.py::_quota_message`) — 1 lần crawl
Douyin có thể ra hàng trăm video, dịch hết ngay lúc crawl sẽ nuốt hết quota
cho những video có thể không bao giờ được chọn đăng.

Dùng `title_vi` (fallback về `title` gốc nếu dịch lỗi) cho:
- Tên dự án pipeline tạo ra khi kích hoạt (bonus phụ: tiếng Việt có dấu qua
  `pj.slugify()` (NFKD + ascii-fold) ra slug ASCII bình thường, KHÔNG bị xoá
  sạch như tiếng Trung — gián tiếp giảm nguy cơ trùng slug đã gặp trước đó).
- Caption khi đăng TikTok (`publish_queue_item_route`).
- Hiển thị trong bảng hàng đợi ở `AutomatedDetailPage.tsx` (tên gốc tiếng
  Trung vẫn hiện dòng phụ bên dưới để đối chiếu).

Video còn `pending` (chưa kích hoạt) vẫn hiện tên gốc tiếng Trung — đúng vì
dịch lười, chưa có bản Việt cho tới khi kích hoạt.

## Phase 2 — Bộ lập lịch tự động (2026-09-23, ĐÃ LÀM XONG)

Người dùng phát hiện qua UI thật: không rõ "video tiếp theo sẽ đăng lúc nào"
— hoá ra đúng, Phase 2 (lịch tự động) trước đó chưa làm, mọi bước (crawl/
kích hoạt/đăng) đều bấm tay. Đã triển khai:

- **Refactor trước khi thêm scheduler**: tách 3 route social (`crawl`,
  `activate`, `publish`) thành 3 hàm dùng chung `_start_social_crawl`,
  `_start_social_activate`, `_start_social_publish` (raise `ValueError` cho
  lỗi tiền kiểm tra thay vì `HTTPException` trực tiếp) — để scheduler nền gọi
  LẠI ĐÚNG logic bấm tay, không viết 2 bản logic dễ lệch pha theo thời gian.
- **`SocialProjectState.last_post_at`** (model mới) — ghi lại mỗi lần đăng
  THÀNH CÔNG, dùng để giãn cách các lần đăng tự động theo đúng `posts_per_day`
  (khoảng cách mục tiêu = 24h / posts_per_day).
- **`_social_scheduler_loop`** — 1 thread daemon khởi động cùng FastAPI
  (`@app.on_event("startup")`), tick mỗi 60s (`SOCIAL_SCHEDULER_TICK_S`), với
  mỗi dự án `status="active"`:
  1. Crawl lại nếu `last_crawl_at` rỗng hoặc quá 24h (`SOCIAL_CRAWL_INTERVAL_H`).
  2. Kích hoạt video `pending` cũ nhất nếu CHƯA có video nào `processing`
     (tránh chạy chồng nhiều pipeline cùng lúc cho 1 dự án).
  3. Đăng video `ready` cũ nhất nếu đã tới khung giờ theo `posts_per_day`.
  Dùng `jobs.any_job_running(f"social:{id}:")` làm khoá đơn giản — chỉ 1
  hành động tự động (crawl HOẶC đăng) chạy cùng lúc cho 1 dự án ở v1 này.
- **Sửa kèm 1 bug thật phát hiện qua ảnh chụp UI**: `QueueItem.error` trước
  đó CHỈ được set khi thất bại, KHÔNG BAO GIỜ được xoá ở nhánh thành công hay
  khi thử đăng lại — khiến lỗi cũ hiện vĩnh viễn trên UI dù video đã đăng
  xong sau đó (thấy rõ: video status "Đã đăng" nhưng vẫn còn dòng lỗi đỏ bên
  dưới). Đã sửa: xoá `error` ngay khi bắt đầu 1 lượt thử đăng mới, và xoá tay
  2 bản ghi lỗi tồn đọng có sẵn trong `workspace/social/2026-09-23_test-ai/`.
- **Frontend**: `AutomatedDetailPage.tsx` hiện dòng ước tính "video tiếp theo
  dự kiến tự đăng lúc..." (tính từ `last_post_at` + 24h/posts_per_day) — ghi
  rõ đây CHỈ LÀ ƯỚC TÍNH, không phải giờ hẹn chính xác (còn phụ thuộc tick +
  có video `ready` sẵn hay chưa).

Đã xác nhận thật: `python -m py_compile` sạch, khởi động uvicorn thật (không
mock) xác nhận thread scheduler chạy không lỗi + `/api/social` trả 200,
`npx tsc -b --noEmit` sạch. CHƯA chạy scheduler đủ lâu (nhiều giờ/24h) để
xác nhận nó thật sự tự crawl/kích hoạt/đăng theo đúng lịch trong điều kiện
thật — khuyến nghị theo dõi log server qua đêm để xác nhận tick chạy đúng.

## 3 bug thật phát hiện qua UI + log lỗi (2026-09-23, sau khi bật Phase 2)

Người dùng gửi ảnh chụp DevTools + log traceback thật, phát hiện 3 vấn đề
sau khi bộ lập lịch bắt đầu tự chạy:

1. **Dịch tiêu đề sai thời điểm**: trước đó dịch NGAY LÚC kích hoạt (`_start_social_activate`)
   — người dùng chốt lại: chỉ dịch NGAY TRƯỚC LÚC đăng bài thật. Đã chuyển
   `translate_stage.translate_title()` sang `_start_social_publish`, dùng lại
   `item.title_vi` nếu đã dịch từ lần thử trước (không gọi lại Gemini). Tên dự
   án tạo lúc kích hoạt quay về dùng `item.title` gốc (không cần bản dịch,
   `aweme_id` đã đủ chống trùng slug). Đã dọn 2 bản `title_vi` bị dịch nhầm từ
   trước (2 video failed-ở-bước-tải, chưa hề đăng) trong dữ liệu thật.
2. **403 Forbidden khi tải video lúc kích hoạt**: traceback thật cho thấy
   `play_url` lấy lúc crawl là link CDN Douyin CÓ CHỮ KÝ HẾT HẠN NHANH — kích
   hoạt trễ (crawl xong nhiều giờ/ngày mới tới lượt, đặc biệt giờ có bộ lập
   lịch tick chậm) khiến link hết hạn, tải thất bại 403. Thêm
   `QueueItem.share_url` (không hết hạn, lấy kèm lúc crawl) — lúc kích hoạt,
   dò lại `play_url` MỚI từ `share_url` qua `douyin_dl_stage.scan_profile_info()`
   ngay trước khi tải, chỉ dùng bản `play_url` cũ làm phương án dự phòng nếu
   dò lại lỗi hoặc video crawl từ trước khi có field `share_url`.
3. **Nút retry sai hành động cho video lỗi ở bước tải**: mọi video `status=failed`
   trước đó đều hiện nút "Đăng lại" (gọi API publish) — kể cả video lỗi NGAY
   Ở BƯỚC TẢI (chưa hề có video xuất ra để đăng), bấm vào chỉ báo lỗi "Không
   tìm thấy video đã xuất". Thêm `QueueItem.failed_stage: "activate"|"publish"`,
   set đúng ở cả 2 nhánh lỗi hiện có (tải video lúc kích hoạt / đăng TikTok
   thất bại), nới điều kiện `_start_social_activate` cho phép kích hoạt LẠI
   video `failed` có `failed_stage="activate"`. Frontend hiện đúng nút "Kích
   hoạt lại" (gọi lại activate) vs "Đăng lại" (gọi publish) theo `failed_stage`.
   Đã gắn `failed_stage="activate"` cho 2 bản ghi lỗi 403 có sẵn trong dữ liệu
   thật để nút hiện đúng ngay không cần đợi thử lại.

**Giới hạn đã biết, CHƯA xử lý** (nói rõ cho người dùng, không âm thầm bỏ
qua): nếu 1 video tự động THẤT BẠI ở giữa pipeline (transcribe/translate/tts/
assemble — không phải ingest hay export) thì hiện KHÔNG có code nào báo
ngược lỗi đó về `QueueItem` — video kẹt vĩnh viễn ở trạng thái `processing`
trên hàng đợi, chỉ phát hiện được bằng cách tự mở đúng project pipeline ra
xem. `_maybe_chain_social` chỉ xử lý 2 mốc THÀNH CÔNG (`assemble`→export,
`export`→ready), chưa có nhánh THẤT BẠI cho các stage giữa. Bộ lập lịch cũng
KHÔNG tự retry video `failed` (chỉ tự động hoá `pending`→kích hoạt và
`ready`→đăng) — tránh vòng lặp thử-lại-vô-hạn nếu lỗi là vĩnh viễn (vd link
hỏng hẳn), chỉ retry được bằng tay qua nút mới.

Đã xác nhận: `py_compile` sạch, khởi động uvicorn thật + gọi
`/api/social/2026-09-23_test-ai` trả 200 (model serialize đúng field mới),
`npx tsc -b --noEmit` sạch.

## Bug thật: video kẹt vĩnh viễn ở "Đang chạy pipeline" khi lỗi giữa chừng (2026-09-24)

Người dùng phát hiện qua UI thật: video "潘达重工..." bị ngắt tiến trình
(backend restart giữa lúc transcribe đang chạy) nhưng hàng đợi VẪN hiện
"Đang chạy pipeline" mãi, không có nút nào để thử lại — đúng như giới hạn đã
ghi nhận trước đó ("mid-pipeline failures not surfaced to queue item").

Nguyên nhân xác nhận qua `project.json` thật: `stages.transcribe.status =
"failed"` (lỗi "Đã dừng theo yêu cầu người dùng") — project TỰ NÓ ghi nhận
đúng lỗi, nhưng KHÔNG CÓ code nào báo ngược lỗi đó về `QueueItem` tương ứng
trong `SocialProjectState` — `_maybe_chain_social` trước đó CHỈ xử lý 2 mốc
THÀNH CÔNG (assemble→export, export→ready), hoàn toàn không có nhánh thất
bại nào.

Đã sửa: thêm `_maybe_fail_social(project_id, stage, error)` — cập nhật
`QueueItem.status=failed`, `failed_stage="activate"` (để nút "Kích hoạt lại"
hiện đúng, tạo lại pipeline từ đầu — hiện CHƯA có cơ chế resume đúng stage
đang dở), gọi song song ở TẤT CẢ điểm 1 stage của project ĐƠN (không phải
multi/split — social project luôn `project_type="single"`) bị đánh dấu thất
bại: `_mark_cancelled` (dùng chung cho huỷ tay MỌI stage), `_mark_export_cancelled`,
và nhánh lỗi thật (không phải huỷ tay) của `_start_transcribe`,
`_start_translate`, `_start_tts`, `_start_assemble`, `_start_export`.

Đã unstick tay video `7373661622264548634` (dữ liệu thật) từ `processing`
kẹt cứng sang `failed`/`failed_stage=activate` để có nút "Kích hoạt lại" ngay,
không cần đợi lỗi lần sau mới có tác dụng.

**Phát hiện thêm (chưa xử lý, cần hỏi người dùng trước khi động vào)**: video
này đã có **7 thư mục project riêng biệt** trong `workspace/projects/` từ
các lần "Kích hoạt lại" trước đó (mỗi lần retry luôn tạo project MỚI, không
tái dùng/dọn project cũ) — không xoá gì, chỉ ghi nhận, vì đây có thể vẫn là
dữ liệu người dùng muốn giữ (chưa hỏi).

## Bỏ CapCut khỏi luồng tự động + settings panel cho từng dự án (2026-09-24)

Người dùng hỏi tại sao video tự động vẫn chạy qua bước "CapCut" trong
pipeline — đúng, đây là lãng phí thật: `export_direct.py::render_video` chỉ
cần `audio/manifest.json` + `sub_vi.srt` (sản phẩm của TTS/translate),
HOÀN TOÀN không phụ thuộc draft CapCut. Đã sửa `_maybe_chain`: khi
`state.social_link` có giá trị, hết TTS là nhảy thẳng sang `_start_export`,
bỏ hẳn `_start_assemble` (CapCut). Project tạo tay vẫn dừng ở CapCut như cũ
(nhánh else giữ nguyên).

Đồng thời thêm **settings panel per-project** ở `/automated/:id`
(`AutomatedDetailPage.tsx`, khối `<details>` mới) — trước đó các field này
(engine, TTS engine + giọng, audio_mode + dB, tốc độ video, nhạc nền + dB,
logo) chỉ sửa được bằng cách chỉnh thẳng file dữ liệu. Theo đúng người dùng
chốt: "chung" = default của `SocialProjectState`/`CreateSocialProjectRequest`
áp dụng lúc TẠO dự án mới, "riêng" = trang chi tiết mỗi dự án tự động sửa
lại tuỳ ý, không liên kết ngược giữa các dự án.

Thêm mới:
- `SocialProjectState.music_volume_db` (mặc định -13dB) + `ProjectState.auto_music_volume_db`
  (Optional, None = dùng hằng số cũ `MUSIC_VOLUME=0.35` cho project tạo tay
  chưa từng chỉnh) — `export_direct.py::render_video` nhận thêm tham số
  `music_volume_db`, áp `volume={db}dB` khi có giá trị.
- Logo/nhạc nền **dùng chung cho CẢ dự án tự động** (không phải riêng từng
  video, vì mỗi lần kích hoạt tạo project MỚI hoàn toàn, không có chỗ bền để
  lưu theo từng video) — file lưu tại `<social_dir>/logo.*`,
  `<social_dir>/music.*`, route mới `/api/social/{id}/logo`,
  `/api/social/{id}/music` (POST/DELETE/GET, mirror y hệt
  `/api/projects/{id}/export/logo`,`.../music`). `_start_export` tự fallback
  về file cấp dự án tự động khi project không có file riêng
  (`_social_asset_path`, mirror `_export_asset_path`).
- `UpdateSocialProjectRequest` thêm `music_volume_db`; wiring đầy đủ ở
  `update_social_route` + lúc kích hoạt (`auto_music_volume_db =
  social_state.music_volume_db`).

Đã xác nhận: `py_compile` sạch, khởi động uvicorn thật + gọi API trả 200,
`npx tsc -b --noEmit` sạch. CHƯA test thật 1 lần xuất video có nhạc nền/logo
dùng chung qua dự án tự động (khuyến nghị thử sau khi kích hoạt video tiếp
theo, xác nhận cả logo lẫn nhạc nền lên đúng, âm lượng nghe hợp lý).

## Hàng đợi lớn bị "chôn" video đang hoạt động + OCR fallback Whisper (2026-09-24)

Dự án tự động thứ 2 của người dùng ("2026-09-24_pokemon", **944 video** trong
hàng đợi, posts_per_day=2) lộ ra 2 vấn đề thật mới:

1. **UI "nhìn như không cập nhật gì"**: bảng hàng đợi sort theo
   `discovered_at` GIẢM DẦN — 8 video mới quét nhất luôn nổi lên đầu, đều
   `pending`. Nhưng bot kích hoạt theo thứ tự CŨ NHẤT trước, nên 5 video có
   hoạt động thật (1 `processing`, 4 `failed`) lại nằm ở top NHỮNG VIDEO CŨ
   NHẤT — tận đáy 1 danh sách 944 dòng, ngoài màn hình đầu tiên hoàn toàn.
   Đã sửa `AutomatedDetailPage.tsx`: sort theo độ ưu tiên trạng thái
   (processing→failed→ready→pending[cũ nhất trước]→posted/skipped), giới hạn
   hiển thị 60 dòng kèm ghi chú số video còn ẩn (tránh trang nặng vì hàng
   trăm/nghìn ảnh thumbnail).
2. **OCR mặc định (đổi hôm qua) fail cứng với video KHÔNG có phụ đề in sẵn**:
   2/5 video lỗi với "Không phát hiện được phụ đề cứng nào trong video" —
   video Pokemon thuần cảnh quay/lồng giọng, không có chữ Hán in trên khung
   hình như video panda trước đó. Người dùng chốt: tự động fallback sang
   Whisper cho ĐÚNG video đó khi gặp lỗi CHÍNH XÁC "không phát hiện được phụ
   đề cứng" (không phải lỗi OCR khác) — chỉ áp dụng cho video do "Dự án tự
   động" sinh ra (`state.social_link` có giá trị), KHÔNG đổi engine mặc định
   của cả dự án. Cài thành vòng lặp thử-lại bên trong CÙNG 1 job
   (`_start_transcribe`'s `target()`) — không gọi lại `_start_transcribe` (sẽ
   bị `jobs.start_job` từ chối vì cùng key job đang "sống", chính là thread
   hiện tại).

Đã xác nhận qua dữ liệu thật (không đoán): `failed_stage` phân biệt đúng
"activate" (lỗi tải/transcribe) vs "publish" (lỗi đăng bài) cho 4 video lỗi
hiện có — code sửa hôm qua (`_maybe_fail_social`) đang hoạt động đúng trên
dự án MỚI này. `py_compile` sạch, `npx tsc -b --noEmit` sạch.

## Khoanh vùng OCR cho Dự án tự động (2026-09-24)

Người dùng phát hiện qua project thật `2026-09-24_pokemon-7685594555621619889-ai`:
video KHÔNG có chữ gì cả nhưng bước transcribe (OCR) vẫn đọc nhầm ra "人" ở
44-46s. Trích khung hình thật xác nhận: cận cảnh Pokemon với giáp/hoạ tiết
góc cạnh phức tạp — model OCR hallucinate ra ký tự Hán với độ tin cậy CAO
(khớp đúng comment cũ trong code: tăng ngưỡng confidence không lọc được loại
nhiễu này).

Vì bước transcribe (dialogue OCR) không thể lọc bằng confidence, hướng khả
thi nhất: **thu hẹp vùng OCR quét TỪ ĐẦU** — đã có sẵn cơ chế `crop_region`
cho project tạo tay (kéo chọn qua `OcrCropSelector`), nhưng CHƯA từng nối vào
"Dự án tự động" (luôn dùng mặc định 25% đáy, không thu hẹp được).

Đã thêm:
- `ProjectState.auto_ocr_crop_region` / `SocialProjectState.ocr_crop_region`
  ([x,y,w,h] phân số, None = mặc định như cũ).
- `detect_subtitle_region()` thêm tham số `search_region` — quét trong vùng
  đã crop (ffmpeg crop trước khi OCR, không phải lọc sau), tự quy đổi toạ độ
  box từ hệ TRONG VÙNG QUÉT sang FULL-FRAME trước khi tính vùng che (trước
  đây hàm này LUÔN quét nguyên khung hình, không nhận tham số thu hẹp).
- Route mới `POST /api/social/{id}/ocr-crop-region` (mirror
  `export/blur-region`), áp dụng cho CẢ bước transcribe (đọc lời thoại) LẪN
  bước tự dò vùng che — copy sang `ProjectState` lúc kích hoạt.
- Frontend: `AutomatedDetailPage.tsx` thêm `OcrCropSelector` trong settings
  panel — mượn video của video GẦN NHẤT đã có pipeline (bất kể trạng thái)
  làm mẫu kéo chọn (dự án tự động không có 1 video cố định).

Đồng thời (câu hỏi trước đó — text nhoè/animation, đã xác nhận qua video
thật) đã thêm cho riêng `detect_subtitle_region` (che phụ đề, KHÔNG áp dụng
được cho transcribe vì confidence không lọc được loại nhiễu này):
- Lọc box theo `result.scores` (RapidOCR) < 0.7 — bỏ khung đang mờ/giữa hiệu
  ứng chuyển động.
- Phát hiện phụ đề DI ĐỘNG qua độ lệch chuẩn tâm-X (> 0.12 chiều rộng khung)
  — bỏ qua auto-che nếu phát hiện, để người dùng tự khoanh tay.
- Đổi hợp min/max thô sang phân vị 10-90% — che khít hơn, không bị 1 khung
  ngoại lệ kéo giãn vùng che cho suốt video.
Đã test thật (trích khung hình + vẽ khung phát hiện) trên 2 video Pokemon
thật khác nhau — vùng che khít đúng, không bị fix mới làm hỏng case bình
thường.

Đã xác nhận: `py_compile` sạch, khởi động uvicorn thật + gọi API social trả
200, `npx tsc -b --noEmit` sạch. CHƯA test thật khoanh vùng OCR mới (`search_region`)
trên video từng bị đọc nhầm — khuyến nghị: sau khi restart backend, vào
project pokemon-7685594555621619889-ai, khoanh vùng OCR hẹp lại rồi kích
hoạt lại 1 video tương tự để xác nhận hết đọc nhầm "人".

## Cỡ chữ phụ đề mới quá to + thêm setting (2026-09-24)

Người dùng gửi ảnh chụp video xuất ra thật — chữ phụ đề Việt to bất cân xứng.
Đo bằng số liệu thật: `FontSize=20` hardcode trong `export_direct.py`, quy
đổi qua hệ toạ độ kịch bản libass 288px (xem `_marginv_units`), ra **~133px
thật** trên video dọc 1920px — gần 1/8 chiều cao khung hình MỖI DÒNG. Đã
trích khung hình thật từ đúng project user gửi ("Người" 1 từ chiếm gần hết
bề ngang khung dọc) xác nhận đúng bug.

Đã thêm field cấu hình `subtitle_font_size` (mặc định 6, theo yêu cầu người
dùng — quy đổi ~40px trên video 1920px) — luồng đầy đủ:
- `export_direct.py::render_video` — tham số mới `subtitle_font_size`
  (hằng số cũ đổi tên `DEFAULT_SUBTITLE_FONT_SIZE=6`), dùng cho cả style
  `FontSize=` LẪN phép tính căn giữa vùng che (trước đó hardcode 20 riêng ở
  đây, dễ lệch pha nếu chỉ sửa 1 chỗ).
- `StartExportRequest.subtitle_font_size` (export thủ công),
  `ProjectState.auto_subtitle_font_size`, `SocialProjectState.subtitle_font_size`,
  `UpdateSocialProjectRequest.subtitle_font_size` — wiring đầy đủ qua
  `_start_export`, `_maybe_chain`, `_maybe_chain_social`, lúc kích hoạt dự án
  tự động.
- UI: `DirectExportPanel.tsx` (project tạo tay) + `AutomatedDetailPage.tsx`
  settings panel (dự án tự động) đều có ô nhập cỡ chữ.

Đã test thật (không đoán): render lại ĐÚNG video + ĐÚNG câu ("Người") user
gửi ảnh, với `subtitle_font_size=6` — trích khung hình cùng mốc thời gian,
xác nhận chữ giờ nhỏ gọn cân đối, không còn chiếm gần hết khung hình.

Lưu ý: field mới chỉ áp dụng cho video xuất TỪ GIỜ trở đi — video/project đã
xuất trước đó (như video trong ảnh chụp) vẫn giữ bản chữ to cũ, cần bấm
"Xuất lại video" ở đúng project đó (hoặc kích hoạt lại qua dự án tự động) để
có bản chữ nhỏ mới.

Đã xác nhận: `py_compile` sạch, khởi động uvicorn thật trả 200,
`npx tsc -b --noEmit` sạch.

## Giảm tải Douyin API bằng dịch vụ bên thứ 3 + rate-limit (2026-09-24)

Sau khi phát hiện tài khoản Douyin bị risk-control tạm khoá API (crawl dồn
dập + mỗi lần kích hoạt là 1 lần gọi API dò lại `play_url`), đã triển khai 4
biện pháp:

1. **Thử link cũ trước, chỉ gọi API khi thất bại** — trước đây LUÔN gọi API
   Douyin dò lại `play_url` trước mỗi lần tải, dù link cũ (từ lúc crawl) đa
   số vẫn còn dùng được nếu kích hoạt sớm. Giờ tải thử trước, hết hạn mới dò
   lại — cắt phần lớn số lần gọi API không cần thiết.
2. **Dịch vụ bên thứ 3 `api3.viesnap.com/douyin/info`** — người dùng tìm
   thấy, đã test thật (không đoán): gọi thành công (200), tải xong 1 video
   thật 29.6MB/99.6s (khớp chính xác `filesize`/`duration` API trả về, xác
   nhận bằng `ffprobe`) — **không cần cookie Douyin của mình**, kể cả với
   link trần `douyin.com/video/{aweme_id}` (không cần `share_url`). Chèn làm
   **tầng dự phòng thứ 2** (giữa "link cũ" và "gọi API Douyin thật") trong
   `_start_social_activate` — `fetch_url.py::download_from_viesnap()`/
   `fetch_douyin_info_viesnap()`. Cùng kiểu bypass Origin/Referer giả đã
   dùng sẵn cho `snaptiktok.to` trong `_download_via_snaptiktok` (không phải
   hướng mới lạ cho codebase này). Không có SLA — lỗi bất kỳ đâu tự rơi
   xuống tầng cuối (Douyin API thật), không raise.
3. **Giãn cách tối thiểu 90s giữa 2 lần gọi API Douyin bất kỳ** (crawl HOẶC
   dò lại play_url) — `_wait_for_douyin_api_slot()`, dùng CHUNG cho mọi dự
   án (risk-control tính theo cookie, không theo dự án).
4. **Tự "nghỉ" 3h khi phát hiện risk-control** — `SocialProjectState.douyin_backoff_until`
   — dò lại 1 video ĐÃ BIẾT tồn tại (`share_url`) mà API Douyin thật vẫn trả
   rỗng = tín hiệu tin cậy, bộ lập lịch bỏ qua crawl/kích hoạt cho dự án đó
   tới hết giờ nghỉ (đăng bài vẫn chạy bình thường, không gọi API Douyin).
5. **Giới hạn số video "sẵn sàng" tối đa 2** (`SOCIAL_MAX_READY_BUFFER`) —
   không kích hoạt dư thừa nhanh hơn tốc độ đăng thật.

Đã đặt `douyin_backoff_until` ngay cho 2 dự án đang bị ảnh hưởng (pokemon,
test-ai, ~3h kể từ 20:44 24/9). Xác nhận thật: khởi động lại server, log
scheduler đúng "đang nghỉ API Douyin tới ... (nghi risk-control)".

Đã xác nhận: `py_compile` sạch (main.py, fetch_url.py), khởi động uvicorn
thật OK. CHƯA test thật toàn bộ chuỗi 3 tầng dự phòng khi tầng 1+2 CÙNG thất
bại (chỉ test riêng lẻ tầng 2 thành công) — nên theo dõi lần kích hoạt tiếp
theo để xác nhận logic rơi tầng đúng như thiết kế.

## 2026-09-25 — Lập lịch giống người hơn (tham khảo bài ieasyclick về risk-control)

1. **Giãn cách gọi API Douyin có ngẫu nhiên** — 90s + 0..60s ngẫu nhiên
   (`DOUYIN_API_JITTER_S`), thay vì đúng 90s.
2. **Chu kỳ crawl 24h ±2h** (`_crawl_due`) — độ lệch suy ra cố định từ
   `last_crawl_at`, không random lại mỗi tick.
3. **Đăng bài theo khung giờ cao điểm** 7-9h, 12-13h, 19-22h
   (`SOCIAL_POSTING_WINDOWS`) — `next_post_at` tính sẵn sau mỗi lần đăng
   thành công bằng `_compute_next_post_at`: giãn cách đo bằng phút-trong-khung
   (6h/ngày ÷ posts_per_day, ±20%). Test 300 lần/mức: mọi giờ hẹn đều rơi
   trong khung, trung bình đúng 1/2/3 bài/ngày. Dự án cũ chưa có
   `next_post_at` được tính bù ở tick đầu. Chưa đăng bài nào → đăng ngay khi
   đang trong khung.
4. **Tối đa 3 bài/ngày** — validate ở schema (`ge=1, le=3`) và ô nhập FE.
5. **Nghỉ tăng dần khi nghi risk-control** — 3h → 12h → 48h theo
   `douyin_backoff_level`, về 0 khi crawl có dữ liệu hoặc dò lại play_url
   thành công. FE hiện cảnh báo khi đang nghỉ.

## 2026-09-25 — Crawl Douyin bằng Chrome thật (`app/stages/douyin_browser.py`)

- Mở Chrome THẬT (`channel="chrome"`, persistent context ở
  `workspace/douyin_chrome_profile`, dùng chung mọi dự án), vào trang kênh,
  cuộn bằng con lăn chuột có nhịp ngẫu nhiên, BẮT response
  `/aweme/v1/web/aweme/post/` do chính trang Douyin tự gọi — không tự tạo chữ
  ký. Dò lại play_url: mở `/video/{aweme_id}`, bắt `/aweme/v1/web/aweme/detail/`,
  không thấy thì fetch đúng API đó từ trong trang.
- Test thật (chưa đăng nhập, chạy như khách): kênh test-ai → 55 video/48s,
  4 response API, 100% có play_url + share_url; dò lại chi tiết 1 video OK.
- play_url từ CDN douyinvod.com trả **403 nếu thiếu Referer** — đã thêm
  `referer: https://www.douyin.com/` vào `download_from_direct_url` (có Referer
  → 200 video/mp4 đủ 38MB). Có thể chính là nguyên nhân một phần các lỗi 403
  "link hết hạn" trước đây.
- Bật theo từng dự án: `SocialProjectState.crawl_via_browser` (mặc định tắt),
  panel "Crawl Douyin bằng Chrome thật" ở trang chi tiết dự án tự động.
- Không điều khiển thẳng profile Chrome mặc định được (Chrome 136+ chặn, và
  profile đang mở bị khoá). Đã thử sao chép phiên từ profile "Thắng ok"
  (`Profile 15`, có đủ sessionid/sid_guard) sang thư mục riêng → THẤT BẠI:
  cookie mã hoá v20 (app-bound encryption) không giải mã được ở thư mục
  user-data khác, Chrome coi như chưa đăng nhập. Đã gỡ chức năng nhập profile;
  chỉ còn đăng nhập 1 lần trong chính profile của app (quét QR).
- Crawl giờ "làm mới" hàng đợi: sau mỗi lần crawl có dữ liệu, bỏ các video
  `pending` KHÔNG có trong lần crawl đó; video đã đăng/đang xử lý/sẵn sàng/
  lỗi/bỏ qua giữ lại làm lịch sử (tránh đăng trùng). Video `pending` có mặt
  lại được thay play_url mới. Test thật qua server (dự án pokemon, crawl 100
  bằng Chrome): 949 → 100 video (87 chờ, 10 lỗi, 2 bỏ qua, 1 đã đăng), bỏ 849.
  Bản sao lưu state trước khi chạy nằm ở scratchpad của phiên làm việc.

## 2026-09-25 — Hàng đợi chung + màn "Giám sát tiến trình"

- Trước: mỗi dự án tự kích hoạt video khi hàng đợi CỦA NÓ rảnh → N dự án =
  N pipeline song song (không có khoá chung nào cho OCR/dịch/TTS/xuất).
- Giờ bộ lập lịch (`_social_scheduler_tick`) chạy hàng đợi CHUNG: toàn hệ
  thống chỉ 1 video xử lý, 1 bài đăng, 1 lượt crawl tại 1 thời điểm.
  - Chọn dự án xử lý tiếp (`_pipeline_plan`): ít video `ready` nhất trước,
    rồi `next_post_at` sớm nhất. Dự án tạm dừng/đang nghỉ Douyin/hết video/
    đủ 2 video ready bị liệt kê kèm lý do.
  - Đăng (`_publish_plan`): chỉ khi tới giờ hẹn VÀ đang trong khung giờ đăng
    (trước đây giờ hẹn qua lâu mà video sẵn sàng lúc 3h sáng là đăng luôn);
    dự án trễ hẹn lâu nhất đăng trước. Tồn đọng tự dồn sang hôm sau.
  - Video `processing` mà pipeline không còn job chạy >3 phút → đánh dấu lỗi
    (`_recover_stuck_items`), tránh chặn cả hàng đợi chung. Test thật: video
    test-ai kẹt từ 23/9 (project không còn tồn tại) được gỡ, hàng đợi lập tức
    chạy video kế tiếp.
  - Kích hoạt lỗi tiền kiểm tra (vd không có link tải) → đánh dấu lỗi luôn
    để không bị chọn lại mãi.
  - Bấm tay "Kích hoạt" ở trang dự án vẫn chạy ngay, không qua hàng đợi.
- `GET /api/social-monitor` + trang `/monitor`: đang chạy (bước + tiến độ),
  thứ tự sắp xử lý, dự án bị chặn + lý do, lịch đăng, lịch crawl, thống kê.
- Test giả lập 100 dự án: 1 tick chỉ kích hoạt 1 video/đăng 1 bài/crawl 1
  kênh; có video đang xử lý → không kích hoạt thêm; 3h sáng → không đăng.

## 2026-09-25 — Tự dọn ổ đĩa (`app/social_cleanup.py`)

Người dùng chốt: thiên hướng tự dọn, mốc 1 ngày. Số đo thật 1 video 7 phút:
thành phẩm 206MB + gốc 87MB + WAV 72MB ≈ 370MB; phụ đề/cấu hình < 5MB.
Profile Chrome TikTok 300-600MB, chủ yếu IndexedDB tiktok.com = video bản
nháp TikTok Studio tự lưu sau các lần đăng bị gián đoạn (472MB ở test-ai).

- Xoá WAV âm thanh nền ngay khi xuất xong (bước xuất tự tạo lại nếu thiếu).
- Mỗi 30 phút, bộ lập lịch dọn file nặng (video gốc, WAV, video xuất) của
  project do dự án tự động sinh ra khi: đã đăng ≥ 24h, hoặc lỗi/bỏ qua/project
  cũ không còn dùng không hoạt động ≥ 24h. Không bao giờ đụng video đang xử
  lý/sẵn sàng, project đang có job, project tạo tay. Đánh dấu
  `QueueItem.files_cleaned_at` (FE hiện "Đã dọn file").
- Dọn cache profile Chrome (TikTok từng dự án + Chrome crawl Douyin) mỗi
  ngày 1 lần khi profile không mở; với TikTok xoá thêm IndexedDB tiktok.com
  nếu ≥ 50MB (chỉ mất bản nháp). Đã chạy thật: test-ai 615MB → 4.9MB, pokemon
  297MB → 2.2MB, Douyin 90MB → 4.4MB; cookie đăng nhập (sessionid) còn nguyên.
- Ổ trống < 20GB → ngừng xử lý video mới (vẫn đăng + dọn), cảnh báo ở Giám sát.
- Lịch dọn (`cleanup_plan`) dùng chung cho dọn thật + hiển thị. Trang riêng
  `/cleanup` "Tự dọn ổ đĩa" ở sidebar (bảng: giờ xoá, dự án, video, lý do,
  dung lượng, nút "Giữ lại"/"Cho phép tự xoá"); trang dự án hiện giờ tự dọn +
  nút giữ lại trên từng video; màn Giám sát chỉ còn dòng tóm tắt + link.
- "Giữ lại" lưu ở `workspace/social_cleanup.json` → `keep_projects` (theo
  project_id); lần dọn gần nhất có giải phóng lưu ở `last_freed`.
- Lượt dọn project đầu tiên chạy thật 14:55 25/9: pokemon-7687455644995746545
  (video lỗi, 328MB) — xoá video/audio, còn phụ đề/cấu hình.
- Xoá ngay nhiều project (`POST /api/cleanup/delete`, `delete_now`): chọn
  nhiều dòng ở trang Tự dọn → "Xoá file ngay" (như tự dọn) hoặc "Xoá hẳn
  project" (xoá thư mục pipeline, bỏ `project_id` ở hàng đợi, giữ trạng thái
  video). Từ chối project hệ thống bảo vệ (đang xử lý/sẵn sàng/đang chạy).
  Test bằng 2 project giả trên trang thật: huỷ xác nhận → không xoá; 2 chế độ
  xoá đúng; 16 project thật nguyên vẹn.
- Sửa mốc "hoạt động gần nhất": trước lấy giờ sửa project.json → bị đẩy lùi
  mỗi khi file bị ghi vì việc không liên quan (vd mở trang project, app tự
  sửa bước bị gián đoạn) → video lỗi có thể không bao giờ được dọn. Giờ lấy
  `created_at` + `stages.*.at` + `export.at` + giờ ghi file video/audio.
  Sau khi sửa, video lỗi pokemon 52MB (xử lý từ 24/9) được dọn đúng lúc 15:23.
- Chưa làm: giảm chất lượng xuất (CRF 20 hiện tại) — chờ người dùng so mẫu.

## 2026-09-25 — Chạy theo thứ tự dự án + dừng dự án khi có video lỗi

Người dùng chốt (2 câu hỏi): (1) dự án A chuẩn bị đủ video cho hôm nay thì
mới sang B, đăng theo lịch riêng từng dự án; (2) video lỗi → DỪNG dự án đó
chờ người dùng xử lý (giữ đúng thứ tự đăng), hàng đợi sang dự án khác.

- `_pipeline_plan`: thứ tự dự án = `created_at`. Cần hôm nay = posts_per_day
  − đã đăng hôm nay. Lượt 1: dự án đầu tiên chưa đủ video sẵn sàng; mọi dự án
  đủ rồi → lượt 2 chuẩn bị trước cho ngày mai (tối đa thêm posts_per_day).
  Thay cho giới hạn cố định 2 video sẵn sàng (đã bỏ `SOCIAL_MAX_READY_BUFFER`).
- Trạng thái trong ngày: đang xử lý / chờ tới lượt / đã chuẩn bị đủ / hoàn
  thành hôm nay / dừng do video lỗi / tạm dừng / nghỉ Douyin / hết video.
- `_publish_plan`: đăng đủ posts_per_day bài trong ngày → "done_today".
- "Kích hoạt lại" video lỗi giờ đưa video về "chờ xử lý" (hàng đợi chung xử
  lý lại đúng lượt) thay vì chạy ngay song song; "Kích hoạt" video chờ chỉ
  chạy ngay khi không có video nào đang xử lý.
- Trang Giám sát: mục "Lượt chạy hôm nay" theo thứ tự dự án.
- Test giả lập 9 kịch bản A/B + giới hạn đăng/ngày: đạt hết.
- Thứ tự video trong dự án: sắp theo NGÀY ĐĂNG DOUYIN (aweme_id tăng dần
  theo thời gian, `_video_age_key`), không theo `discovered_at`. Lý do: mỗi
  lần crawl Douyin trả video mới nhất trước, `discovered_at` tăng dần theo
  đúng thứ tự đó → trong 1 lần crawl video MỚI NHẤT bị coi là "cũ nhất" (xác
  nhận trên pokemon: "cũ nhất" cũ là 7686807…, cũ thật là 7665926…). Áp dụng
  cho cả xử lý, đăng và danh sách hàng đợi (mặc định cũ → mới).
- ĐÃ SỬA (25/9 16:25) lỗi xuất "[WinError 206] The filename or extension is
  too long": dòng lệnh ffmpeg vượt giới hạn 32.767 ký tự của Windows (mỗi câu
  giọng đọc là 1 `-i` đường dẫn đầy đủ + hàng trăm bộ lọc viết thẳng trên
  lệnh; ca thật: 147 đầu vào, 442 bộ lọc). `export_direct.py`: trộn trước
  giọng đọc theo nhóm 80 câu (`_premix_voices`, WAV trung gian) + bộ lọc ghi
  ra file (`-/filter_complex <file>`, ffmpeg 8.1). Ca thật xam-xi-du: còn 4
  đầu vào, 145 câu trong 2 nhóm, xuất 379s đủ hình + tiếng; chạy lại export
  trên project → done, video trong hàng đợi chuyển ready.

## 2026-09-24 — Sửa OCR đọc thiếu quá nhiều (dự án `xa-xi-1`)

**Triệu chứng**: `http://localhost:5175/projects/2026-09-24_xa-xi-1` — `sub_zh.srt`
có câu kéo dài bất thường (1 câu 32s, 1 câu 64s), rõ ràng thiếu nhiều lời
thoại thật.

**Nguyên nhân (xác nhận bằng ảnh khung hình thật + log)**: `progress` lưu
trong `project.json` cho thấy chỉ **6/109** khung thật sự được OCR (103 khung
bị bỏ qua vì cơ chế tối ưu tốc độ coi là "giống khung trước"). Cơ chế này
(`_frame_signature`/`_frames_similar` trong `transcribe_ocr.py`) so % khác
biệt pixel TRÊN TOÀN VÙNG CROP — khi không truyền `crop_region` tay,
`transcribe_video()` mặc định dùng `OCR_CROP_BOTTOM_FRACTION=0.25` (25% đáy
TOÀN khung hình, rất rộng với video ngang 1920x1080). Vùng chữ phụ đề thật
chỉ chiếm phần nhỏ trong vùng crop rộng đó, nên 2 câu phụ đề khác hẳn nội
dung vẫn bị tính "giống nhau" (dưới ngưỡng 2%) → bỏ qua OCR, tái dùng chữ cũ
sai. Trích khung thật ở giây 15 xác nhận phụ đề đã đổi hẳn sang "你过来我砍死你"
nhưng `sub_zh.srt` vẫn ghi nguyên câu cũ suốt 13→32s.

**Fix**: `app/main.py::_start_transcribe` — khi `engine == "ocr"` và KHÔNG có
`crop_region` tay, tự lấy `export_blur_region` (nếu lượt dò sớm sau ingest,
`_start_early_blur_detect`, đã kịp xong) hoặc tự gọi
`ocr_stage.detect_subtitle_region(video_path)` để dò vùng khít, dùng vùng
khít đó làm `crop_region` cho `transcribe_video()` thay vì để `None` (rơi vào
mặc định 25% đáy rộng). Vùng khít làm % khác biệt tập trung đúng vào chữ,
nhạy đúng với thay đổi nội dung thật.

**Test thật đã xác nhận**: chạy lại `transcribe_video()` trên đúng video
`xa-xi-1` với vùng khít đã dò được (`[0.02, 0.837, 0.96, 0.059]` — ~6% chiều
cao khung hình, thay vì 25%) → khung thật được OCR tăng từ 6/109 lên
**13/109**, số câu tăng từ 3 lên **10 câu**, và câu bị thiếu trước đó
("你过来我砍死你", xuất hiện thật ở giây 15) giờ đã xuất hiện đúng trong SRT ở
mốc 13s→32s. `py_compile` + `from app import main` (93 route, không đổi) đều
sạch.

## Kiểm tra (Phase 1)

- `python -c "from app import main"` sau mỗi lần sửa backend.
- Test thật: tạo 1 cặp với link Douyin đã dùng trước đó → Crawl → xác nhận có
  video mới, crawl lại lần 2 không bị nhân đôi. Kích hoạt 1 video → theo dõi
  tới khi `ready`, mở project được tạo ra kiểm tra video xuất ra đúng như làm
  tay.
- `cd frontend && npx tsc -b --noEmit`.
