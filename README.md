# AI review mã so với người review

Đề tài **C3** — Chuyên đề 1, thạc sỹ Khoa học Máy tính. Nghiên cứu thực nghiệm trên pull request của các dự án Java mã nguồn mở.

> **Trạng thái (2026-11-22):** đã có thiết kế (bản 3) và kế hoạch triển khai, **chưa có code**. Đang chờ cả nhóm review tài liệu.

## Đề tài trong một đoạn

Đề bài: *chạy công cụ AI review trên các pull request đã có nhận xét của người; đo mức trùng khớp và tỉ lệ cảnh báo nhiễu.* Nhóm cho ba mô hình (Claude qua Claude Code CLI, GPT qua Codex CLI, DeepSeek qua API) review các PR Java, so góp ý của AI với nhận xét của người review gốc, và đo cả hai phía: AI bắt lại được bao nhiêu vấn đề người đã nêu, bao nhiêu góp ý của AI là nhiễu, và người review bỏ sót bao nhiêu vấn đề hợp lệ mà AI tìm ra. Nhãn cốt lõi lấy từ AACR-Bench (do 80 kỹ sư xác minh); nhóm tự chấm phần còn lại sau khi hiệu chuẩn với nhãn chuyên gia.

## Tài liệu cần đọc (theo thứ tự)

1. **[Thiết kế](docs/specs/2026-11-21-c3-ai-code-review-design.md)** — câu hỏi nghiên cứu (RQ1–RQ6), ba bộ dữ liệu D1/D2/D3, cách chạy AI, chấm tay, thống kê, mối đe dọa đến tính hợp lệ, đối chiếu tiêu chí chấm của thầy.
2. **[Nghiên cứu liên quan](docs/related-work.md)** — SWE-PRBench, SWR-Bench, AACR-Bench, c-CRAB, CRJudgeBench… và khoảng trống đề tài lấp.
3. **[Kế hoạch triển khai](docs/superpowers/plans/2026-11-22-c3-pipeline.md)** — 14 task code, mỗi task có test; cuối file là phần việc của nhóm (không phải code).

## Khi review, mọi người chú ý giúp

- **Mục 5.10 của thiết kế** — các quyết định chi tiết đang được chốt thay nhóm (tiêu chí "hợp lệ", ngưỡng kappa, cách nới khi thiếu dữ liệu, lựa chọn mô hình hoãn đến sau pilot…). Đồng ý hay không?
- **Mục 5.3** — chạy Claude/GPT bằng **gói đăng ký CLI** của thành viên: ai có Claude Code, ai có Codex, ai sẽ giữ tài khoản chạy cho từng công cụ?
- **Mục 5.5** — chấm tay: mỗi người khoảng 15–20 giờ (ước tính); có kịp không?
- **Mục 6** — phân vai: mỗi người nhận vai nào.
- **Mục 8** — thiết kế đã đáp ứng tiêu chí chấm của thầy chưa.

Góp ý bằng cách **mở Issue** hoặc **comment trên commit/file** trong GitHub. Muốn push code thì gửi username GitHub để được thêm làm collaborator.

## Quy ước

- Không push thẳng vào `main`: mỗi task một nhánh, mở pull request, một người khác duyệt.
- **Không bao giờ commit khóa API hay token** (file `.env` đã nằm trong `.gitignore`). Repo này công khai.
- Dữ liệu công bố phải ẩn danh tên người dùng GitHub của người review.
