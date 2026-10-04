import { type ReactNode } from 'react'
import { Link } from 'react-router-dom'

/** Trang "Hướng dẫn sử dụng" — tĩnh, không gọi backend. Viết cho USER (dùng từ
 * thân thiện, không lộ tên engine kỹ thuật). Mục tiêu: người mới đọc 1 lượt là
 * làm được từ lúc cài tới lúc ra video. Giữ ngắn, đánh số, nhiều ví dụ cụ thể. */

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <section className="card space-y-4 p-5">
      <div>
        <h2 className="font-heading text-lg font-medium">{title}</h2>
        {subtitle && <p className="mt-0.5 text-sm text-neutral-400">{subtitle}</p>}
      </div>
      {children}
    </section>
  )
}

function Step({ n, title, children }: { n: number; title: string; children?: ReactNode }) {
  return (
    <div className="flex gap-3">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent-900/70 text-sm font-medium text-accent-200">
        {n}
      </span>
      <div className="min-w-0 flex-1 pt-0.5">
        <p className="text-sm font-medium text-text">{title}</p>
        {children && <div className="mt-1 space-y-1 text-sm text-neutral-400">{children}</div>}
      </div>
    </div>
  )
}

/** Nhãn 1 nút/menu trong app — in đậm nhẹ để người đọc dễ dò trên màn hình. */
function Ui({ children }: { children: ReactNode }) {
  return <span className="rounded bg-neutral-800 px-1.5 py-0.5 text-[13px] font-medium text-neutral-200">{children}</span>
}

export default function GuidePage() {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="space-y-1">
        <h1 className="font-heading text-2xl font-medium">Hướng dẫn sử dụng</h1>
        <p className="text-sm text-neutral-400">
          App biến video tiếng Trung thành video tiếng Việt: nhận diện lời thoại → dịch → lồng giọng đọc → xuất video.
          Làm theo thứ tự bên dưới là xong.
        </p>
      </header>

      {/* Cách nhanh nhất — để ngay đầu cho người lười đọc hết */}
      <section className="rounded-lg border border-accent/40 bg-accent-900/20 p-5">
        <h2 className="font-heading text-lg font-medium text-accent-200">Cách nhanh nhất (gần như tự động)</h2>
        <p className="mt-2 text-sm text-neutral-300">
          Vào <Ui>Dự án</Ui> → tạo dự án → thêm video gốc → bật <Ui>Tự động chạy hết pipeline</Ui>. App tự chạy hết các
          bước và <b>xuất ra video tiếng Việt (file final.mp4)</b>, giữ nguyên âm thanh gốc. Bạn chỉ cần ngồi chờ rồi bấm{' '}
          <Ui>Mở thư mục</Ui> để lấy video.
        </p>
      </section>

      <Section title="1. Cài đặt lần đầu" subtitle="Chỉ làm 1 lần cho mỗi máy.">
        <div className="space-y-4">
          <Step n={1} title="Tải bộ cài">
            <p>Mở link bộ cài admin gửi (file tên có dạng OddlyLabReup-Setup-…​.exe) rồi tải về.</p>
          </Step>
          <Step n={2} title="Chạy file vừa tải">
            <p>
              Windows có thể báo <i>“Windows protected your PC / Unknown publisher”</i> — đây là cảnh báo bình thường vì
              app chưa mua chứng chỉ. Bấm <Ui>More info</Ui> → <Ui>Run anyway</Ui> để chạy tiếp.
            </p>
          </Step>
          <Step n={3} title="Chờ tải thành phần chạy">
            <p>
              Lần đầu máy sẽ tải bộ thư viện AI (vài GB) — chỉ tải 1 lần, lần sau mở nhanh. Để yên cho tải xong.
            </p>
          </Step>
          <Step n={4} title="Mở app">
            <p>
              Sau khi cài xong, mở app bằng shortcut <b>OddlyLab Reup</b> ngoài Desktop hoặc trong Start Menu. App mở ra
              trong trình duyệt.
            </p>
          </Step>
        </div>
      </Section>

      <Section title="2. Đăng ký & đăng nhập" subtitle="Mỗi tài khoản dùng trên 1 máy.">
        <div className="space-y-4">
          <Step n={1} title="Bấm Đăng ký">
            <p>
              Ở màn đăng nhập, bấm <Ui>Chưa có tài khoản? Đăng ký</Ui>.
            </p>
          </Step>
          <Step n={2} title="Điền thông tin">
            <p>Nhập email, mật khẩu, tên hiển thị và số liên hệ (Zalo/điện thoại) rồi gửi yêu cầu.</p>
          </Step>
          <Step n={3} title="Chờ admin duyệt">
            <p>Admin sẽ duyệt và cấp quyền cho tài khoản của bạn. Chưa duyệt thì chưa đăng nhập được.</p>
          </Step>
          <Step n={4} title="Đăng nhập">
            <p>Duyệt xong, quay lại màn đăng nhập, nhập email + mật khẩu vừa tạo là vào được.</p>
          </Step>
        </div>
        <p className="rounded-md border border-divider bg-neutral-900/40 p-3 text-xs text-neutral-400">
          Nếu màn đăng nhập hiện dòng <Ui>Có bản mới</Ui> ở trên cùng, bấm <Ui>Cập nhật</Ui> trước rồi hãy đăng nhập —
          để chắc chắn bạn đang dùng bản mới nhất.
        </p>
      </Section>

      <Section title="3. Làm 1 video (các bước trong dự án)" subtitle="Nếu không bật chế độ tự động, bạn bấm từng bước theo thứ tự.">
        <div className="space-y-4">
          <Step n={1} title="Tạo dự án">
            <p>
              Vào <Ui>Dự án</Ui> → bấm tạo dự án mới, đặt tên.
            </p>
          </Step>
          <Step n={2} title="Thêm video gốc">
            <p>Dán link Douyin/TikTok hoặc upload file video từ máy.</p>
          </Step>
          <Step n={3} title="Nhận diện lời thoại">
            <p>App nghe video và ghi lại lời thoại tiếng Trung.</p>
          </Step>
          <Step n={4} title="Dịch">
            <p>
              Dịch lời thoại sang tiếng Việt. Bạn có thể <b>sửa tay từng câu</b> hoặc bấm dịch lại 1 câu nếu chưa ưng.
            </p>
          </Step>
          <Step n={5} title="Giọng đọc">
            <p>Tạo giọng đọc tiếng Việt cho các câu đã dịch. Chọn giọng ở phần cài đặt của dự án.</p>
          </Step>
          <Step n={6} title="Xuất video">
            <p>
              Bấm <Ui>Xuất video</Ui> (không qua CapCut) để ra thẳng file <b>final.mp4</b>: đã lồng giọng, có phụ đề mới,
              che phụ đề cũ. Nếu muốn tự chỉnh thêm trong CapCut thì bấm <Ui>Dựng CapCut</Ui> thay vì xuất.
            </p>
          </Step>
          <Step n={7} title="Lấy video ra">
            <p>
              Xuất xong bấm <Ui>Mở thư mục</Ui> — Explorer mở ngay tới file final.mp4 để bạn dùng/đăng.
            </p>
          </Step>
        </div>
      </Section>

      <Section title="4. Video quá dài? Chia thành nhiều đoạn" subtitle="CapCut có thể không load nổi video quá dài.">
        <p className="text-sm text-neutral-400">
          Trong dự án có mục <Ui>Chia video thành nhiều đoạn</Ui>: cắt video dài thành nhiều đoạn nhỏ. Khi bật tự động,
          mỗi đoạn tự chạy hết và <b>tự xuất video riêng</b> (chạy lần lượt, đoạn sau bắt đầu khi đoạn trước xong). Mỗi
          đoạn vẫn có nút <Ui>Xuất video</Ui> và <Ui>Mở thư mục</Ui> riêng.
        </p>
      </Section>

      <Section title="5. Đăng lên TikTok / Facebook" subtitle="Nếu tài khoản của bạn được cấp quyền đăng.">
        <div className="space-y-4">
          <Step n={1} title="Kết nối tài khoản">
            <p>
              Vào <Link to="/accounts" className="text-accent-300 underline-offset-2 hover:underline">Tài khoản</Link> →
              đăng nhập TikTok/Facebook 1 lần (app nhớ phiên, lần sau không phải đăng nhập lại).
            </p>
          </Step>
          <Step n={2} title="Đăng từ dự án">
            <p>Xuất video xong, trong dự án bấm nút đăng TikTok/Facebook, điền mô tả rồi đăng.</p>
          </Step>
        </div>
      </Section>

      <Section title="6. Các trang khác trong menu">
        <ul className="space-y-2 text-sm text-neutral-400">
          <li><b className="text-neutral-200">Dự án tự động</b> — tự tìm video theo kênh/nguồn rồi xử lý và đăng, chạy nền không cần ngồi canh.</li>
          <li><b className="text-neutral-200">Làm sạch video</b> — xoá chữ/phụ đề cứng in sẵn trên video gốc.</li>
          <li><b className="text-neutral-200">Ghép video</b> — nối nhiều video lại thành một.</li>
          <li><b className="text-neutral-200">Tải video</b> — tải video từ link về máy.</li>
          <li><b className="text-neutral-200">Giám sát tiến trình</b> — xem các việc đang chạy.</li>
          <li><b className="text-neutral-200">Tự dọn ổ đĩa</b> — xoá file tạm cũ cho nhẹ máy.</li>
          <li><b className="text-neutral-200">Cài đặt</b> — chọn giọng đọc, cách nhận diện, thư mục CapCut, API…</li>
          <li><b className="text-neutral-200">Kiểm tra hệ thống</b> — xem máy đã đủ điều kiện chạy chưa (GPU, ffmpeg, dữ liệu AI…).</li>
        </ul>
      </Section>

      <Section title="7. Gặp trục trặc?">
        <ul className="space-y-2 text-sm text-neutral-400">
          <li>
            <b className="text-neutral-200">Thấy “Có bản mới”</b> → bấm <Ui>Cập nhật</Ui> rồi <Ui>Khởi động lại</Ui>. Luôn
            dùng bản mới nhất để đỡ lỗi.
          </li>
          <li>
            <b className="text-neutral-200">Mạng chậm khi đăng/tải</b> → cứ để app chạy, đừng đóng giữa chừng; app tự chờ
            tải xong.
          </li>
          <li>
            <b className="text-neutral-200">Quên mật khẩu</b> → liên hệ admin để cấp lại.
          </li>
          <li>
            <b className="text-neutral-200">Tắt máy/nghỉ</b> → đóng trình duyệt <i>không</i> tắt app (app vẫn chạy nền để
            Dự án tự động hoạt động). Muốn dừng hẳn, bấm <Ui>Tắt app</Ui> ở menu bên trái.
          </li>
          <li>
            <b className="text-neutral-200">Bị khoá/hết hạn</b> → liên hệ admin. Thay đổi quyền/hạn có thể mất tới ~5 phút
            mới áp vào máy bạn.
          </li>
        </ul>
      </Section>

      <p className="pb-2 text-center text-xs text-neutral-500">
        Còn vướng chỗ nào, chụp màn hình gửi admin — sẽ được hỗ trợ nhanh nhất.
      </p>
    </div>
  )
}
