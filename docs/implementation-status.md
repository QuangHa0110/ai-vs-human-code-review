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
| 9 | Checkout và chạy review | `task09_resume_light` (GPT-6 Luna) | Qua review đặc tả và chất lượng; 25 test riêng đạt |
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
Task 3–8 đã qua review; suite **169 test đạt, 1 test dữ liệu bỏ qua khi thiếu nguồn cục bộ** trước task 9. Task 10–12 và 14 chưa triển khai; task 13 tiếp tục để sau.

## Bàn giao khi tạm dừng — 2026-11-25

Người dùng yêu cầu dừng để tắt máy. Agent triển khai task 9 đã được ngắt.
Không chạy mô hình thật. Giữ nguyên các thay đổi đang dở trên nhánh `feat/c3-pipeline`.

- Task 9: `checkout.py`, `review.py`, CLI review và các test đã viết trên đĩa, chưa commit/chưa được chấp nhận qua hai vòng review.
- Lead chạy test tập trung gần nhất: **23 passed**; con số này chưa xác nhận các sửa đổi cuối sau đó.
- Agent đang xử lý sửa đổi cuối: SHA đúng 40 ký tự; D2 yêu cầu trực tiếp danh sách `retained_pr_ids`, không fallback `selected_pr_ids`.
- Agent báo đã sửa artifact JSON không phải object, checkout tạm trùng số PR giữa repo và lỗi probe phiên bản CLI; cần reviewer xác minh lại trên trạng thái cuối.
- Khi tiếp tục: kiểm tra git diff, chạy lại test task 9 và toàn suite; giao review đặc tả lại rồi review chất lượng, sửa lỗi trước commit. Sau đó task 10–12 và 14; task 13 vẫn để sau.
- Theo yêu cầu mới của người dùng, tiếp tục dùng subagent với model nhẹ hơn.

## Tiếp tục — 2026-11-25

Task 9 đã sửa xong SHA 40 ký tự, manifest D2, artifact không phải object, thư mục checkout tạm và xác minh phiên bản CLI. Hai vòng review độc lập đã chấp nhận. Lead chạy toàn bộ suite: 194 passed, 1 skipped trước khi commit task 9.
