# Báo cáo công việc triển khai pipeline C3

- **Ngày lập:** 25/11/2026
- **Nhánh:** `feat/c3-pipeline`
- **Mốc mã nguồn được kiểm tra:** `245a2b0`
- **Phạm vi:** 14 task implement trong [kế hoạch C3](superpowers/plans/2026-11-22-c3-pipeline.md). Báo cáo này ghi nhận phần mềm đã xây dựng; kết quả thực nghiệm và kết luận nghiên cứu chưa thuộc phạm vi hoàn thành.

## 1. Kết quả tổng quan

Đã triển khai đủ **14/14 task** của kế hoạch: nạp ba nguồn dữ liệu, thu thập PR Java, chạy và lưu kết quả AI review, so khớp với tham chiếu, tính chỉ số và kiểm định, tạo báo cáo, cùng bộ công cụ bàn giao chấm tay. Mã được tách thành gói Python `c3`; các bước giao tiếp bằng dữ liệu JSON trong `data/` và ghi báo cáo vào `results/`.

Kiểm thử tại mốc mã nguồn trên: **281 passed, 1 skipped** bằng lệnh `.venv/bin/python -m pytest -q -rs`. Test bị bỏ qua cần thư mục AACR-Bench v1 thật qua biến `C3_AACR_V1_DIR`; các test còn lại dùng fixture, dữ liệu giả hoặc mock phù hợp. `uv lock --check`, `uv run c3 sample --help` và `git diff --check` đều đạt. Các kết quả này xác nhận hành vi phần mềm được kiểm thử, **chưa xác nhận chất lượng review của mô hình trên dữ liệu thật**.

## 2. Công việc đã thực hiện

| Task | Nội dung triển khai | Minh chứng commit |
|---|---|---|
| 1 | Khung dự án, cấu hình `.env`, mô hình dữ liệu PR/nhận xét, đọc ghi JSON | `8f17332` |
| 2 | Runner Claude CLI, Codex CLI, DeepSeek API và OpenAI API dự phòng; cache và metadata lần chạy | `8f17332` |
| 3 | GitHub client có phân trang, xử lý rate limit và lấy diff | `d878bf8` |
| 4 | Lọc diff Java, làm sạch nhận xét vòng review đầu và đếm lý do loại | `90f64ad` |
| 5 | Chọn PR D2 theo tiêu chí, lấy mẫu có seed, ẩn danh login, lệnh `collect` | `e638aee` |
| 6 | Nạp AACR-Bench Java cho D1, kiểm tra split và ghi nguồn gốc dữ liệu | `0ddd80b` |
| 7 | Nạp snapshot và annotation SWE-PRBench cho D3 | `941e159` |
| 8 | Phân loại tham chiếu người review; giữ nguyên nhãn tham chiếu D1/D3 | `a6cded4` |
| 9 | Checkout đúng commit được review, hai điều kiện diff/agent, lưu artifact và tiếp tục lần chạy lỗi | `67fb656` |
| 10 | Giám khảo so khớp AI với tham chiếu, kiểm tra artifact và khóa định danh | `81271b7` |
| 11 | Recall, tỉ lệ không khớp, tập vấn đề gộp, độ dao động giữa các lần chạy | `eeaa410` |
| 12 | Cochran's Q, McNemar exact, Holm, bootstrap theo PR và phân tích lực từ tham số pilot | `c927c1d` |
| 13 | Mẫu chấm mù `run1/diff`, tập hiệu chuẩn, phân người chấm, ghép nhãn sau phân xử | `245a2b0` |
| 14 | Lệnh `analyze`, bảng và biểu đồ RQ1–RQ6, trạng thái dữ liệu thiếu, hướng dẫn chạy lại | `6e1dcda`; tích hợp nhãn tại `245a2b0` |

Task 13 được triển khai sau Task 14 theo thứ tự điều phối thực tế. Lần tích hợp cuối cập nhật báo cáo để nhận nhãn của các góp ý AI **không khớp** trong `run1/diff`; D1 yêu cầu đủ nhãn để mở rộng tập vấn đề RQ5, còn D2 ghi rõ các ước lượng từ mẫu. Khoảng tin cậy của các chỉ số có nhãn dùng bootstrap theo PR khi đủ PR đóng góp. Chỉ số thiếu dữ liệu được để trống kèm trạng thái thay vì tự suy đoán nhãn.

## 3. Kiểm soát chất lượng và khả năng tái lập

- **Dữ liệu và riêng tư:** D2 chỉ giữ PR Java đủ điều kiện; login người review được ẩn danh. File bàn giao chấm tay che nguồn reviewer/mô hình; khóa ánh xạ nằm ở thư mục riêng tư bị Git bỏ qua. [Ghi chú dữ liệu](data-notes.md) và [hướng dẫn chấm tay](annotation-guide.md) nêu cấu trúc, điều kiện và cách bàn giao.
- **Chạy mô hình:** Kết quả lưu reviewer, backend, model, phiên bản công cụ, cờ gọi, thời điểm và usage khi có. Lỗi CLI, JSON không hợp lệ hoặc artifact thiếu không được tính như một review không có góp ý; cache không lưu kết quả lỗi. Chế độ agent checkout đúng commit cần review để tránh thấy code xuất hiện sau đó.
- **So khớp và thống kê:** Artifact review/judgment được kiểm tra định danh và fingerprint trước phân tích. Recall không đếm trùng tham chiếu; khoảng tin cậy và so sánh ghép cặp lấy PR làm đơn vị khi phương pháp yêu cầu. Báo cáo nêu rõ lỗi chạy, lỗi parse, thiếu artifact và trạng thái nhãn.
- **Review mã:** Mỗi task đã qua review đặc tả và chất lượng trong quá trình triển khai; các lỗi được phát hiện ở cache, resume, bàn giao nhãn và phạm vi tính chỉ số đã được sửa trước commit. [Nhật ký tiến độ](implementation-status.md) ghi các mốc kiểm tra.
- **Cài đặt:** `pyproject.toml` đăng ký lệnh `c3`; `uv.lock` khóa bộ phụ thuộc đã kiểm tra. README mô tả `uv sync --extra dev`, cấu hình `.env` và thứ tự các lệnh. Dữ liệu tải về, kết quả và bí mật được loại khỏi Git.

## 4. Giới hạn hiện tại và việc cần nhóm thực hiện

1. **Pilot và mô hình:** Chưa chọn hoặc ghim phiên bản mô hình. Nhóm cần thống nhất tài khoản, backend, prompt và cấu hình đọc an toàn, sau đó chạy pilot để lấy tham số cỡ mẫu. Đường Codex CLI cho điều kiện chỉ đọc diff hiện dừng ở preflight vì chưa chứng minh được việc tắt toàn bộ công cụ; cấu hình có backend OpenAI API dự phòng nhưng cũng chưa chọn mô hình.
2. **Dữ liệu thật:** Cần đặt nguồn AACR-Bench v1 tại `C3_AACR_V1_DIR`, xác nhận dữ liệu D1/D3 và thu thập D2 theo mốc thời gian đã thống nhất. Một test dữ liệu thật hiện bị bỏ qua khi thiếu nguồn cục bộ.
3. **Thực nghiệm:** Chưa gọi mô hình để tạo review hoặc judgment trên bộ thí nghiệm chính; chưa có số liệu RQ1–RQ6 để diễn giải. Kiểm chứng thực tế các ràng buộc công cụ CLI vẫn cần thực hiện trước khi chạy chính.
4. **Chấm tay:** Chưa có người chấm, nhãn hay phân xử. Nhóm cần chốt cỡ mẫu D2, hiệu chuẩn người chấm theo ngưỡng kappa, gán hai người chấm độc lập cho mỗi mục, phân xử bất đồng rồi mới chạy `merge-labels` và `analyze --labels-dir`.
5. **Hiệu chuẩn D3 và công bố:** Khoảng tin cậy benchmark D3 phải được cung cấp riêng; việc công bố tập dữ liệu D2 cần rà soát ẩn danh và quyền sử dụng dữ liệu.

## 5. Bàn giao sử dụng

1. Cài đặt bằng `uv sync --extra dev`, tạo `.env` từ `.env.example` và đọc [README](../README.md).
2. Kiểm tra lệnh bằng `uv run c3 --help`; xem từng bước với `uv run c3 <command> --help`.
3. Khi pilot, dữ liệu và cấu hình được chốt, chạy chuỗi `load-aacr` / `load-swepr` / `collect` → `classify` → `review` → `judge` → `power` → `sample` / `calibration-set` → chấm và phân xử → `merge-labels` → `analyze`.
4. Bàn giao riêng [hướng dẫn chấm tay](annotation-guide.md) cho nhóm; **không chia sẻ** `results/annotation-private/`, `.env` hay dữ liệu PR chưa được duyệt công bố.

**Kết luận:** Phần implement trong kế hoạch đã hoàn tất và có thể bàn giao để chuẩn bị pilot. Mức độ sẵn sàng hiện tại là **sẵn sàng vận hành thử quy trình phần mềm**, chưa phải kết quả nghiên cứu đã được xác nhận trên dữ liệu thật.
