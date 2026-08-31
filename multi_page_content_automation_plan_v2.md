# Tool Quản Lý & Tự Động Hóa Nội Dung Cho Nhiều Facebook Page

> Phiên bản: 2.0  
> Mục tiêu: khoảng 50 Facebook Page, mỗi Page 1–2 Reel/ngày  
> Phạm vi: Kiến trúc, flow, các Phase phát triển, nguồn content, công nghệ và hạ tầng  
> Lưu ý: Chưa xây dựng DB schema chi tiết và chưa viết code implementation.

---

# 1. Tổng quan dự án

Hệ thống là một tool quản lý và tự động hóa nội dung cho nhiều Facebook Page.

## Mục tiêu chính

- Kết nối và quản lý nhiều Facebook Page.
- Mỗi Page có cấu hình nội dung riêng.
- Tự động tìm kiếm content mới từ nhiều nguồn.
- Có thứ tự ưu tiên nguồn content.
- Tự động chuyển sang nguồn tiếp theo nếu nguồn ưu tiên không đủ content.
- Không lấy lại content đã từng xử lý.
- Tự động xếp lịch 1–2 Reel/Page/ngày.
- Tự động đăng Reel thông qua Meta API trong phạm vi API/quyền được hỗ trợ.
- Sau khi đăng thành công, xóa video tạm khỏi VPS để tránh đầy ổ đĩa.
- Có Queue/Worker để xử lý các tác vụ nền.
- Đủ đơn giản để vận hành khoảng 50 Page mà chưa cần Kubernetes hay kiến trúc distributed phức tạp.

Mục tiêu sản lượng:

```text
50 Page
×
1–2 Reel/ngày
=
50–100 Reel/ngày
```

Đây là mức tải phù hợp với một **modular monolith + background workers + Redis Queue + PostgreSQL**.

---

# 2. Kiến trúc tổng thể

Không nên thiết kế theo kiểu mỗi Page có một crawler riêng:

```text
Page A → Crawler A
Page B → Crawler B
Page C → Crawler C
...
Page 50 → Crawler 50
```

Thay vào đó, dùng một **Content Engine dùng chung**:

```text
                    CONTENT ENGINE
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
       TikTok           YouTube           BaoMoi
          │                │                │
          └────────────────┼────────────────┘
                           ▼
                    Chuẩn hóa content
                           │
                           ▼
                         Dedup
                           │
                           ▼
                    Content Pool chung
                           │
                           ▼
                    Page Matching
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
          Page A         Page B        Page ...50
            │              │              │
            └──────────────┼──────────────┘
                           ▼
                        Queue
                           │
                           ▼
                    Publish Worker
                           │
                           ▼
                      Facebook
```

Điểm quan trọng:

- **Page là một cấu hình**, không phải một crawler instance.
- Crawler và Content Engine được dùng chung.
- Mỗi Page có profile riêng để quyết định content nào phù hợp.
- Việc đăng được xử lý bằng Queue/Worker.

---

# 3. Page Profile

Mỗi Page là một đơn vị cấu hình.

Concept:

```text
Page
├── Facebook Connection
│   ├── pageId
│   ├── pageName
│   └── pageAccessToken
│
├── Content Profile
│   ├── keywords
│   ├── keyword groups
│   ├── keyword priority
│   ├── language
│   ├── region
│   └── quality rules
│
├── Source Priority
│   ├── TikTok       priority 1
│   ├── YouTube      priority 2
│   └── BaoMoi       priority 3
│
├── Automation
│   ├── enabled
│   ├── dailyTarget
│   └── schedules
│
└── Publishing
    ├── queued
    ├── scheduled
    ├── published
    └── failed
```

Mỗi Page có thể:

- Bật/tắt automation.
- Bật/tắt từng source.
- Thay đổi thứ tự ưu tiên source.
- Thay đổi keyword.
- Thay đổi target Reel/ngày.
- Thay đổi lịch đăng.

---

# 4. Source Priority — thiết kế chính thức

Thứ tự nguồn mặc định:

```text
1. TikTok
2. YouTube
3. BaoMoi
```

Đây là **thứ tự fallback**, không phải yêu cầu phải crawl cả ba nguồn.

## Ví dụ 1 — TikTok đủ content

Page cần 2 Reel:

```text
Target = 2

TikTok
→ tìm được 2 content mới
→ lấy 2
→ STOP
```

Không cần crawl YouTube và BaoMoi.

## Ví dụ 2 — TikTok thiếu content

```text
Target = 2

TikTok
→ tìm được 1 content mới

Còn thiếu = 1

YouTube
→ tìm được 4 content mới

Lấy 1
→ STOP
```

## Ví dụ 3 — TikTok và YouTube đều thiếu

```text
Target = 2

TikTok
→ 0

YouTube
→ 1

Còn thiếu = 1

BaoMoi
→ tìm kiếm candidate phù hợp
```

Nếu BaoMoi không cung cấp được media phù hợp cho Reel:

```text
Kết quả = 1 Reel
```

Không ép hệ thống đăng content kém chất lượng chỉ để đủ quota.

---

# 5. Source Priority và Keyword Priority

Hai loại priority phải tách riêng.

## Source Priority

```text
TikTok
↓
YouTube
↓
BaoMoi
```

## Keyword Priority

```text
Keyword Group P1
↓
Keyword Group P2
↓
Keyword Group P3
```

Ví dụ:

```text
Page A

Source:
P1 = TikTok
P2 = YouTube
P3 = BaoMoi

Keyword:
P1:
  crazy challenge
  extreme challenge

P2:
  viral challenge
  funny challenge

P3:
  amazing challenge
```

Content Engine có thể xử lý theo logic:

```text
for source in sourcePriority:

    for keywordGroup in keywordPriority:

        search

        kiểm tra content đã biết
        validate
        filter
        score
        select

        nếu đủ target:
            STOP
```

Cần giữ logic này đơn giản và dễ kiểm soát trong MVP.

---

# 6. Source Adapter Architecture

Mỗi nguồn có một Adapter riêng.

```text
ContentSource
├── TikTokAdapter
├── YouTubeAdapter
└── BaoMoiAdapter
```

Các Adapter có chung một interface ở mức kiến trúc:

```text
search(SearchContext)
    ↓
NormalizedContent[]
```

Kết quả sau khi chuẩn hóa:

```text
NormalizedContent
├── source
├── sourceId
├── sourceUrl
├── title
├── description
├── author
├── publishedAt
├── mediaType
├── mediaUrl / mediaReference
├── thumbnail
└── metadata
```

Content Engine không nên chứa logic riêng của từng nền tảng.

Lợi ích:

- Dễ thay đổi cách crawl một source.
- Dễ thêm source mới.
- Không làm source-specific logic lan ra toàn hệ thống.

Sau này có thể thêm:

```text
RedditAdapter
InstagramAdapter
XAdapter
OtherNewsAdapter
```

mà không phải thay đổi toàn bộ Content Engine.

---

# 7. Chiến lược công nghệ cho từng nguồn

## 7.1 TikTok

Ưu tiên API chính thức nếu use case và quyền truy cập cho phép.

TikTok có các API/Research Tools cho việc truy vấn public video trong những trường hợp được cấp quyền phù hợp.

Tuy nhiên không được thiết kế hệ thống với giả định mọi ứng dụng đều chắc chắn có quyền truy cập API phù hợp.

Kiến trúc nên là:

```text
TikTokAdapter
├── Official API implementation
└── phương án thay thế nếu hợp pháp và được phép
```

Không nên để toàn bộ hệ thống phụ thuộc cứng vào một phương pháp crawl duy nhất.

---

# 7.2 YouTube

Ưu tiên:

```text
YouTube Data API v3
```

Cho discovery có thể sử dụng các tham số như:

```text
q
type=video
publishedAfter
publishedBefore
order=date
regionCode
relevanceLanguage
```

Không nên search tất cả keyword của 50 Page một cách độc lập nếu các Page có keyword trùng nhau.

Nên có:

```text
Global query cache
+
Query deduplication
+
Discovery window
+
Page matching
```

Mục tiêu là:

```text
Một query
→ lấy content chung
→ lưu Content Pool
→ nhiều Page sử dụng
```

thay vì:

```text
Page A → query
Page B → query giống hệt
Page C → query giống hệt
...
```

---

# 7.3 BaoMoi

BaoMoi nên được xem trước hết là:

```text
Nguồn discovery/news
```

không mặc định là:

```text
Nguồn video Reel
```

Flow:

```text
BaoMoi
 ↓
Article
 ↓
title
URL
publisher
published time
media reference nếu có
 ↓
Candidate
```

Chỉ đưa vào Reel pipeline khi media thực tế phù hợp với quy trình và việc sử dụng media đó được phép.

Nếu chỉ có bài viết:

```text
BaoMoi
→ content discovery
→ không tự động biến thành Reel video
```

Cần tôn trọng:

- robots.txt
- điều khoản sử dụng
- giới hạn truy cập
- bản quyền và quyền sử dụng nội dung

Không nên giả định rằng một trang public nghĩa là có thể tải và đăng lại toàn bộ nội dung.

---

# 8. Content Identity và Dedup

MVP dùng identity:

```text
source + sourceId
```

Ví dụ:

```text
tiktok:123456789
youtube:abcXYZ
baomoi:article_123
```

Concept unique:

```text
UNIQUE(source, sourceId)
```

Điều này giải quyết:

> Content trên cùng một source đã từng được hệ thống biết hay chưa?

---

# 9. Publication History

Không gộp trạng thái published vào Content.

Một content có thể được sử dụng ở nhiều Page:

```text
Content A
   │
   ├── Page A → published
   ├── Page B → published
   └── Page C → unused
```

Vì vậy cần tách:

```text
Content
```

và:

```text
Content Publication
```

Concept:

```text
Content
   ↓
Publication History
   ├── Page A
   ├── Page B
   └── Page C
```

MVP chỉ cần exact identity.

Cross-platform fingerprint như:

```text
perceptual hash
audio fingerprint
video fingerprint
```

nên để Phase sau.

---

# 10. Global Content Pool

Content được crawl nên đi vào pool chung:

```text
TikTok
YouTube
BaoMoi
   │
   ▼
Global Content Pool
   │
   ├── normalized metadata
   ├── source identity
   ├── quality state
   ├── tags/topics
   └── discoveredAt
```

Sau đó:

```text
Global Content Pool
        ↓
Page Matching
        ↓
Page-specific Queue
```

Điều này đặc biệt quan trọng khi có 50 Page.

---

# 11. Không download tất cả candidate

Không nên:

```text
Crawler
→ tìm 100 video
→ download 100 video
→ sau đó mới chọn 2
```

Nên:

```text
Search
 ↓
Metadata
 ↓
Identity check
 ↓
Basic validation
 ↓
Filter
 ↓
Rank
 ↓
Chọn candidate
 ↓
Download media
```

Lợi ích:

- Giảm bandwidth.
- Giảm CPU.
- Giảm disk I/O.
- Giảm temporary files.
- Giảm số lần download lỗi.

---

# 12. Content Selection Pipeline

Flow đề xuất:

```text
SEARCH
  ↓
NORMALIZE
  ↓
IDENTITY CHECK
  ↓
VALIDATE METADATA
  ↓
FILTER
  ↓
QUALITY SCORE
  ↓
SELECT
  ↓
DOWNLOAD MEDIA
  ↓
MEDIA VALIDATION
  ↓
QUEUE
```

Các filter có thể gồm:

```text
- độ mới
- ngôn ngữ
- khu vực
- duration
- resolution
- title quality
- thiếu media
- duplicate
- đã sử dụng
- source restriction
```

---

# 13. Scheduler

Scheduler phải nằm ở backend.

Frontend chỉ cấu hình lịch.

Không để browser chịu trách nhiệm:

```text
setTimeout()
setInterval()
```

cho các tác vụ production.

Flow:

```text
Scheduler
   ↓
Tìm job đến hạn
   ↓
Lock job
   ↓
Đẩy vào Publish Queue
   ↓
Worker xử lý
```

Mỗi Page có thể có:

```text
dailyTarget = 1 hoặc 2

schedule:
  10:00
  19:00

timezone:
  Asia/Ho_Chi_Minh
```

Timezone phải được cấu hình rõ ràng.

---

# 14. Queue và Worker

Không để API request trực tiếp thực hiện toàn bộ quy trình:

```text
crawl
→ download
→ FFmpeg
→ upload Facebook
```

Thay vào đó:

```text
API
 ↓
Create Job
 ↓
Redis / BullMQ
 ↓
Worker
```

Các loại job:

```text
crawl
content processing
download
media processing
publish
retry
cleanup
analytics
```

---

# 15. Reel Publishing Lifecycle

Flow đề xuất:

```text
QUEUED
  ↓
DOWNLOADING
  ↓
MEDIA_VALIDATING
  ↓
PROCESSING
  ↓
UPLOADING
  ↓
FACEBOOK_PROCESSING
  ↓
PUBLISHING
  ↓
PUBLISHED
```

Nếu lỗi:

```text
FAILED
  ↓
RETRY
```

Retry phải có giới hạn.

Các lỗi có thể retry:

```text
network timeout
temporary server error
temporary rate limit
temporary Facebook error
```

Các lỗi không nên retry vô hạn:

```text
invalid token
permission revoked
invalid media
unsupported format
permanent API error
```

---

# 16. Temporary Video Storage

Video không cần lưu lâu dài trên VPS.

Flow:

```text
Download
   ↓
Temporary file
   ↓
Optional FFmpeg processing
   ↓
Facebook upload
   ↓
Facebook publish confirmed
   ↓
Save Facebook ID
   ↓
DELETE temporary file
```

Không xóa ngay sau khi upload nếu Facebook vẫn còn bước processing/publishing.

Chỉ xóa khi job đạt trạng thái thành công cuối cùng.

Ví dụ:

```text
UPLOADING
   ↓
UPLOADED
   ↓
FACEBOOK_PROCESSING
   ↓
PUBLISHED
   ↓
DELETE FILE
```

---

# 17. Cleanup Worker

Cần có Cleanup Worker như một lớp bảo vệ.

Ví dụ:

```text
PUBLISHED
→ xóa ngay

FAILED + quá TTL
→ xóa

ORPHAN FILE + quá TTL
→ xóa

UNKNOWN TEMP FILE + quá TTL
→ xóa
```

TTL có thể bắt đầu ở mức:

```text
6–24 giờ
```

Mục đích:

- VPS không bị đầy disk khi worker crash.
- Server restart không để lại hàng nghìn file.
- Có thể tự dọn các file không còn liên kết với job.

---

# 18. Media Processing

Không phải video nào cũng trực tiếp phù hợp để publish.

Media Worker cần kiểm tra:

```text
container
codec
duration
width
height
file size
audio
```

Dùng:

```text
FFmpeg
FFprobe
```

Flow:

```text
Source media
   ↓
Validate
   ↓
Đã tương thích?
 ├── YES → Upload
 └── NO
      ↓
    FFmpeg
      ↓
    Upload
```

Không nên re-encode mọi video nếu không cần.

---

# 19. Facebook Publishing Abstraction

Nên tách publisher:

```text
Publisher
├── FacebookPostPublisher
└── FacebookReelPublisher
```

Lợi ích:

- Post và Reel có flow riêng.
- Dễ retry.
- Dễ thêm loại content khác.
- Không để logic Facebook nằm trực tiếp trong scheduler.

---

# 20. Facebook Connection và Token

Phase đầu cần chứng minh:

```text
Facebook OAuth
    ↓
User authorization
    ↓
Get Pages
    ↓
Page Access Token
    ↓
Select Pages
```

Token lifecycle phải được quản lý:

```text
valid
expired/revoked
permission changed
reconnect required
```

Không đưa token vào frontend log.

Token/secrets phải được lưu an toàn và mã hóa khi cần.

---

# 21. Monitoring

Với khoảng 50 Page, monitoring là bắt buộc.

Dashboard tổng quan:

```text
Pages: 50
Active: 47
Attention: 3

Today's target: 100
Scheduled: 78
Published: 65
Pending: 13
Failed: 0
```

Per Page:

```text
Page A
├── Connection: OK
├── Content: 2/2
├── Reel 1: Published
├── Reel 2: Scheduled
└── Source fallback: TikTok
```

Phân loại lỗi:

```text
SOURCE_ERROR
RATE_LIMIT
NO_CONTENT
INVALID_CONTENT
DOWNLOAD_ERROR
MEDIA_PROCESSING_ERROR
FACEBOOK_TOKEN_ERROR
FACEBOOK_PERMISSION_ERROR
FACEBOOK_UPLOAD_ERROR
FACEBOOK_PUBLISH_ERROR
```

---

# 22. Logging và Observability

Mọi background job nên có thể truy vết:

```text
jobId
pageId
contentId
source
sourceId
facebookId
retryCount
duration
status
errorCode
```

Ví dụ:

```text
jobId
  ↓
pageId
  ↓
contentId
  ↓
sourceId
  ↓
facebookPostId
```

Điều này rất quan trọng khi hệ thống chạy đồng thời cho 50 Page.

---

# 23. Công nghệ đề xuất

## Backend

```text
NestJS
TypeScript
```

Lý do:

- Modular architecture.
- Dependency Injection.
- Phù hợp Adapter/Service/Worker.
- Dễ chia module.
- Dễ mở rộng.

---

## Database

```text
PostgreSQL
```

Dùng cho:

- Page.
- Facebook connection.
- Content profile.
- Source configuration.
- Content metadata.
- Publication history.
- Schedule.
- Job history.
- Analytics.

Không lưu binary video vào PostgreSQL.

---

## Queue

```text
Redis
+
BullMQ
```

Dùng cho:

```text
crawl
download
processing
FFmpeg
publish
retry
cleanup
analytics
```

---

## Frontend

```text
React
TypeScript
Vite
Tailwind CSS
```

Các màn hình chính:

```text
Dashboard
Pages
Page Detail
Content
Queue
Schedule
Automation
Errors
Analytics
Settings
```

---

## Crawler / HTTP

Ưu tiên theo thứ tự:

```text
Official API
    ↓
RSS / Sitemap / public feed
    ↓
HTTP + Cheerio
    ↓
Playwright
```

Không dùng Playwright cho mọi source.

Playwright chỉ dùng khi thực sự cần xử lý trang JavaScript-heavy và việc truy cập đó phù hợp với điều khoản/robots/permissions của source.

---

## Media

```text
FFmpeg
FFprobe
```

Dùng cho:

- kiểm tra media
- lấy metadata
- chuyển format
- resize
- encode

---

## Deploy

```text
Docker
Docker Compose
```

MVP có thể chạy:

```text
docker-compose
├── api
├── worker
├── scheduler
├── postgres
├── redis
└── frontend
```

Không cần Kubernetes ở giai đoạn đầu.

---

# 24. Hạ tầng VPS

Cấu hình khởi đầu đề xuất:

```text
CPU: 4 vCPU
RAM: 8 GB
SSD: 80–100 GB NVMe
OS: Ubuntu 24.04 LTS
Network: bandwidth tốt
```

Nếu muốn tiết kiệm:

```text
2 vCPU
4 GB RAM
60 GB NVMe
```

cũng có thể bắt đầu nếu concurrency thấp và media processing nhẹ.

## GPU

Không cần GPU cho:

```text
Crawler
NestJS
PostgreSQL
Redis
BullMQ
FFmpeg thông thường
Facebook API
```

GPU chỉ cần cân nhắc nếu sau này chạy local:

```text
LLM
Whisper
AI image generation
AI video generation
computer vision
```

---

# 25. Worker Concurrency

Không chạy 50 Page cùng lúc.

Ví dụ ban đầu:

```text
Crawler workers: 3–5
Download workers: 2–3
FFmpeg workers: 1–2
Publish workers: 2–5
```

Sau đó điều chỉnh dựa trên:

```text
CPU
RAM
network
source rate limit
Facebook API behavior
queue latency
```

Ví dụ:

```text
50 Page
   ↓
100 jobs/day
   ↓
Queue
   ↓
Workers xử lý lần lượt
```

Không cần 50 process.

---

# 26. Storage Strategy

Không lưu video lâu dài.

Ví dụ:

```text
/tmp/content-jobs/
├── downloads/
├── processing/
└── uploads/
```

Flow:

```text
Download
 ↓
downloads/
 ↓
processing/
 ↓
uploads/
 ↓
Facebook success
 ↓
DELETE
```

Database chỉ lưu metadata:

```text
source
sourceId
sourceUrl
title
author
discoveredAt
pageId
facebookPostId
publishedAt
status
```

Không lưu video binary trong database.

---

# 27. Bandwidth

Ví dụ giả định:

```text
100 video/ngày
×
50 MB/video
=
5 GB download/ngày
```

Upload Facebook cũng tạo traffic tương ứng.

Do đó khi chọn VPS cần xem:

```text
CPU
RAM
Disk
+
Bandwidth / traffic allowance
```

Không chỉ nhìn CPU/RAM.

---

# 28. Phase phát triển

## Phase 0 — Technical Validation

Mục tiêu: kiểm chứng các điểm rủi ro lớn trước khi xây hệ thống.

Kiểm tra:

```text
1. Facebook OAuth
2. Lấy danh sách Page
3. Page Access Token
4. Test Facebook Post
5. Test Facebook Reel
6. Test YouTube discovery
7. Test TikTok access method
8. Test BaoMoi discovery
9. Download → temporary storage → upload → delete
10. Kiểm tra VPS network/storage
```

Deliverable:

```text
Proof of Concept nhỏ
```

Chưa cần Dashboard.

Đây là Phase bắt buộc.

---

# 29. Phase 1 — Foundation

Xây:

```text
NestJS
PostgreSQL
Redis
BullMQ
Docker Compose
Configuration
Secrets
Logging
Health checks
```

Định nghĩa ở mức kiến trúc:

```text
SourceAdapter interface
Publisher interface
Worker architecture
```

Deliverable:

```text
Backend chạy được background jobs ổn định.
```

---

# 30. Phase 2 — Facebook Page Management

Xây:

```text
Facebook OAuth
Page discovery
Page connection
Token management
Page list
Page detail
Enable/disable automation
```

Deliverable:

```text
User có thể kết nối và quản lý nhiều Page.
```

---

# 31. Phase 3 — Content Profile

Xây:

```text
Keywords
Keyword groups
Keyword priority
Source priority
Daily target
Schedule
Language
Region
Quality rules
```

Ví dụ:

```text
Page A

Target: 2/day

Sources:
1. TikTok
2. YouTube
3. BaoMoi

Keywords:
P1:
  crazy challenge
  extreme challenge

P2:
  viral challenge
  funny challenge
```

---

# 32. Phase 4 — Content Engine

Xây:

```text
SourceAdapter
TikTokAdapter
YouTubeAdapter
BaoMoiAdapter

Normalization
Identity
Dedup
Validation
Global Content Pool
```

Chưa tự động publish.

Deliverable:

```text
System có thể tìm và lưu candidate hợp lệ.
```

---

# 33. Phase 5 — Source Priority + Fallback Engine

Implement logic chính:

```text
Target = 2

TikTok
→ lấy tối đa phần có thể

Nếu còn thiếu:
    YouTube

Nếu còn thiếu:
    BaoMoi

Nếu vẫn thiếu:
    STOP
```

Keyword cũng có fallback:

```text
Keyword P1
→ P2
→ P3
```

Nguyên tắc:

```text
Chất lượng > đủ quota
```

---

# 34. Phase 6 — Page Matching + Content Queue

Flow:

```text
Global Content Pool
        ↓
Page Matching
        ↓
Page Queue
```

Mỗi Page chỉ nhận candidate phù hợp.

Các trạng thái có thể gồm:

```text
QUEUED
READY
SCHEDULED
REJECTED
```

---

# 35. Phase 7 — Scheduler + Worker

Xây:

```text
Scheduler
↓
Due jobs
↓
Redis/BullMQ
↓
Worker
```

Bổ sung:

```text
job locking
retry
backoff
failed state
job timeout
idempotency
```

Mục tiêu:

```text
Worker crash
→ job không mất
→ có thể retry
```

---

# 36. Phase 8 — Facebook Post Publishing

Trước khi hoàn thiện Reel, triển khai Post publisher:

```text
Facebook Post Publisher
```

Test:

```text
text
image
video
```

Mục tiêu:

```text
Kiểm chứng end-to-end publishing
```

---

# 37. Phase 9 — Facebook Reel Publishing

Đây là Phase quan trọng.

Flow:

```text
Queue
 ↓
Download
 ↓
Validate
 ↓
FFmpeg nếu cần
 ↓
Upload
 ↓
Facebook processing
 ↓
Publish
 ↓
Save Facebook ID
 ↓
Delete temporary video
```

Cần test:

```text
success
timeout
retry
invalid media
Facebook error
token error
worker restart
```

---

# 38. Phase 10 — Automation End-to-End

Flow hoàn chỉnh:

```text
Daily Scheduler
 ↓
Source Priority
 ↓
Fallback
 ↓
Content Queue
 ↓
Schedule
 ↓
Reel Publishing
 ↓
Cleanup
```

Không test thẳng 50 Page.

Tăng dần:

```text
1 Page
 ↓
3 Pages
 ↓
10 Pages
 ↓
50 Pages
```

---

# 39. Phase 11 — Monitoring + Analytics

Bổ sung:

```text
Job monitoring
Source health
Page health
Publish success/failure
Token status
Content statistics
Basic Page analytics
Post/Reel analytics
```

Analytics không được làm block publishing pipeline.

---

# 40. Phase 12 — Optimization

Chỉ tối ưu sâu sau khi hệ thống chạy ổn định.

Có thể thêm:

```text
Global query cache
Source query dedup
Content reuse
Worker tuning
Batch processing
Advanced scheduling
Perceptual video fingerprint
Advanced content scoring
```

Không thêm Kubernetes chỉ vì có 50 Page.

---

# 41. MVP Definition

MVP nên bao gồm:

```text
✓ Facebook OAuth
✓ Multiple Pages
✓ Page connection
✓ Page Profile
✓ Multiple keyword groups
✓ Keyword priority
✓ Source priority
✓ TikTok source
✓ YouTube fallback
✓ BaoMoi discovery adapter
✓ Content identity
✓ Basic dedup
✓ Global Content Pool
✓ Fallback Engine
✓ Page Matching
✓ Content Queue
✓ Scheduler
✓ Facebook Post
✓ Facebook Reel
✓ Temporary media storage
✓ Delete after successful publish
✓ Retry
✓ Basic monitoring
✓ 1–2 Reel/Page/day
```

Chưa cần:

```text
✗ Kubernetes
✗ Microservices
✗ Distributed database
✗ Local LLM
✗ AI video generation
✗ Advanced video fingerprinting
✗ Complex recommendation engine
✗ Advanced analytics
```

---

# 42. Kiến trúc cuối cùng

```text
                         FRONTEND
                  React + TypeScript
                           │
                           ▼
                      NestJS API
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
   Page Manager      Content Engine        Analytics
        │                  │
        │          ┌───────┼────────┐
        │          ▼       ▼        ▼
        │       TikTok  YouTube  BaoMoi
        │          │       │        │
        │          └───────┼────────┘
        │                  ▼
        │           Normalize/Dedup
        │                  │
        │                  ▼
        │           Global Content Pool
        │                  │
        │                  ▼
        │            Fallback Engine
        │                  │
        │                  ▼
        │              Page Matching
        │                  │
        └──────────────────┤
                           ▼
                     Redis / BullMQ
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
           Crawler       Media        Publish
           Worker        Worker        Worker
                           │
                         FFmpeg
                           │
                           ▼
                        Meta API
                           │
                 ┌─────────┼─────────┐
                 ▼         ▼         ▼
               Page A    Page B   Page ...50
```

---

# 43. Nguyên tắc thiết kế quan trọng

1. Page Profile là cấu hình, không phải một crawler instance.
2. Crawler infrastructure được dùng chung.
3. Source priority phải cấu hình được.
4. Fallback chỉ chạy khi source trước đó không đủ content.
5. Chất lượng content quan trọng hơn việc cố đủ quota.
6. Content identity tối thiểu là `source + sourceId`.
7. Publication history tách khỏi Content identity.
8. Không download candidate trước khi chọn.
9. Video chỉ là temporary media.
10. Chỉ xóa video sau khi Facebook publish thành công được xác nhận.
11. Tất cả tác vụ lâu đều chạy qua Queue/Worker.
12. Token và secrets chỉ được xử lý ở backend.
13. Ưu tiên Official API trước.
14. Nếu không có API phù hợp thì ưu tiên public feed/sitemap/HTTP parser phù hợp với quyền sử dụng.
15. Playwright chỉ dùng khi thực sự cần.
16. Bắt đầu bằng Modular Monolith.
17. Không dùng Kubernetes nếu workload thực tế chưa yêu cầu.
18. Global Content Pool giúp tránh crawl lặp lại giữa nhiều Page.
19. Source Priority và Keyword Priority là hai hệ thống độc lập.
20. BaoMoi là nguồn discovery/news; chỉ trở thành nguồn Reel khi media thực tế phù hợp và được phép sử dụng.

---

# 44. Rủi ro kỹ thuật cần ưu tiên kiểm chứng

Theo thứ tự:

```text
1. Facebook OAuth / permissions / token lifecycle
2. Facebook Reel publishing
3. TikTok access / API eligibility
4. Quyền và điều khoản của các source
5. Media download reliability
6. Source rate limits
7. Duplicate content
8. Temporary file cleanup
9. Job idempotency
10. Worker crash/restart
11. Bandwidth
12. VPS resource usage
```

Đây là những điểm cần kiểm chứng thực tế trước khi scale lên 50 Page.

---

# 45. Thứ tự triển khai thực tế

```text
Phase 0
Technical Validation
        ↓
Phase 1
Foundation
        ↓
Phase 2
Facebook Page Management
        ↓
Phase 3
Content Profile
        ↓
Phase 4
Content Engine
        ↓
Phase 5
Source Priority + Fallback
        ↓
Phase 6
Content Queue + Page Matching
        ↓
Phase 7
Scheduler + Worker
        ↓
Phase 8
Facebook Post
        ↓
Phase 9
Facebook Reel
        ↓
Phase 10
End-to-End Automation
        ↓
Phase 11
Monitoring + Analytics
        ↓
Phase 12
Optimization
```

---

# 46. Kết luận

Với mục tiêu khoảng 50 Page và 1–2 Reel/Page/ngày, kiến trúc phù hợp nhất ở giai đoạn đầu là:

```text
React
+
NestJS
+
PostgreSQL
+
Redis/BullMQ
+
FFmpeg
+
Docker Compose
+
1 VPS
```

Mô hình vận hành:

```text
TikTok
   ↓
Nếu đủ content → STOP
   ↓
Nếu thiếu
   ↓
YouTube
   ↓
Nếu đủ → STOP
   ↓
Nếu thiếu
   ↓
BaoMoi
   ↓
Nếu vẫn thiếu → không ép đăng
```

Media:

```text
Download
→ Process
→ Facebook
→ Confirm Published
→ Delete temporary file
```

Hạ tầng ban đầu:

```text
4 vCPU
8 GB RAM
80–100 GB NVMe
Ubuntu 24.04
```

Đây là cấu hình có headroom tốt cho khoảng 50 Page; có thể bắt đầu thấp hơn nếu cần tiết kiệm.

DB schema chi tiết, API design, folder structure và code implementation sẽ được thiết kế ở bước tiếp theo, sau khi Phase 0 và kiến trúc này được chốt.
