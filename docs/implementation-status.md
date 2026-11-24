# Tiến độ triển khai C3

Lead điều phối trong cuộc trò chuyện hiện tại. Nhánh làm việc: `feat/c3-pipeline`.
Không chạy thí nghiệm chính trước khi pilot chốt mô hình và cấu hình.

## Phân công

| Task | Phạm vi | Agent | Trạng thái |
|---|---|---|---|
| 1 | Khung Python, cấu hình, dữ liệu, JSON | `task01_foundation` | Qua review đặc tả và chất lượng; 21 test đạt |
| 2 | Runner CLI/API, cache, metadata | `task02_runner_recon` | Qua review đặc tả và chất lượng; 28 test đạt |
| 3 | GitHub client | `task03_github` | Qua hai vòng review; 16 test đạt |
| 4 | Làm sạch nhận xét và diff Java | `task04_clean` | Qua hai vòng review; 20 test đạt |
| 5 | Chọn PR, ẩn danh, CLI collect | `task05_collect` | Qua hai vòng review; toàn suite 110 test đạt |
| 6 | Nạp AACR-Bench D1 | `task06_aacr` | Qua hai vòng review; nguồn thật xác minh riêng |
| 7 | Nạp SWE-PRBench D3 | `task07_swepr` | Qua hai vòng review |
| 8 | Phân loại nhận xét | `task08_classify` | Qua hai vòng review; D1/D3 giữ nguyên tham chiếu |
| 9 | Checkout và chạy review | `task09_review` | Đang triển khai |
| 10 | Giám khảo so khớp | Chưa giao | Chờ kết quả review |
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
Task 3–8 đã qua review; suite **169 test đạt, 1 test dữ liệu bỏ qua khi thiếu nguồn cục bộ**. Task 9 đang triển khai. Task 10–12 và 14 chưa triển khai; task 13 tiếp tục để sau.
