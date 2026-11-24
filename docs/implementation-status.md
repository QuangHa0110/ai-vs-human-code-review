# Tiến độ triển khai C3

Lead điều phối trong cuộc trò chuyện hiện tại. Nhánh làm việc: `feat/c3-pipeline`.
Không chạy thí nghiệm chính trước khi pilot chốt mô hình và cấu hình.

## Phân công

| Task | Phạm vi | Agent | Trạng thái |
|---|---|---|---|
| 1 | Khung Python, cấu hình, dữ liệu, JSON | `task01_foundation` | Qua review đặc tả và chất lượng; 21 test đạt |
| 2 | Runner CLI/API, cache, metadata | `task02_runner_recon` | Qua review đặc tả và chất lượng; 28 test đạt |
| 3–5 | GitHub, làm sạch, thu thập D2 | Chưa giao | Chờ nền tảng |
| 6–7 | Nạp và xác minh D1/D3 | Chưa giao | Chờ nền tảng |
| 8–10 | Phân loại, checkout, review, giám khảo | Chưa giao | Chờ runner và dữ liệu |
| 11–12 | Chỉ số, kiểm định, phân tích lực | Chưa giao | Chờ mô hình kết quả |
| 13 | Công cụ chấm tay | Để sau theo yêu cầu | Hoãn |
| 14 | Tích hợp CLI, xuất báo cáo, hướng dẫn | Chưa giao | Chờ các module |

## Kiểm soát triển khai

- Mỗi task có agent theo nhiệm vụ; kiểm tra đặc tả rồi chất lượng trước khi chấp nhận.
- Sửa thiết kế cache để lần chạy/attempt khác nhau không dùng chung kết quả.
- Dùng lệnh môi trường Python phù hợp macOS.
- Ghi rõ bước kiểm chứng dữ liệu hoặc CLI chưa thực hiện; test giả không chứng minh điều kiện thực nghiệm thật.
- Task 1 đã qua hai vòng review độc lập. Task 2 triển khai sau nền tảng, không sửa file task 1.

## Khả năng theo dõi trong app

Công cụ điều khiển máy từ chối truy cập `com.openai.codex` vì giới hạn an toàn.
Chưa tạo được project hay các chat sidebar riêng như ảnh người dùng yêu cầu.
Người dùng đã chọn tiếp tục bằng subagent trong phiên điều phối; đây không phải các chat sidebar riêng.

## Kiểm chứng CLI

- Đã đọc help CLI cài trên máy; chưa gọi mô hình thật.
- Codex diff-only trả lỗi preflight rõ ràng do chưa xác minh được chế độ tắt toàn bộ công cụ. Backend API dự phòng phải được chọn rõ trong cấu hình.
- Chưa chốt mô hình, prompt thực nghiệm hoặc dữ liệu chính.

## Kết quả đợt đầu

Lead chạy lại `.venv/bin/python -m pytest -q`: **49 passed**.
Lỗi payload Claude `null` được reviewer phát hiện, agent sửa và reviewer kiểm tra lại đạt.
Task 3–12 và 14 chưa triển khai; task 13 tiếp tục để sau.
