# Thiết kế đề tài C3 — Review mã tự động bằng AI so với người review

Ngày: 2026-11-21 · Thời hạn: ~2 tuần (đến khoảng 2026-12-05)

## 1. Mục đích và lý do

Đề tài C3 thuộc Chuyên đề 1 (thạc sỹ). Yêu cầu của đề: *chạy công cụ AI review trên các pull request đã có nhận xét của người; đo mức trùng khớp và tỉ lệ cảnh báo nhiễu.*

Câu hỏi nghiên cứu:
- **RQ1 — Trùng khớp:** Trong các vấn đề mà người review đã chỉ ra, AI phát hiện lại được bao nhiêu phần trăm?
- **RQ2 — Nhiễu:** Trong các góp ý của AI, bao nhiêu phần trăm không tương ứng với nhận xét nào của người, và trong số đó bao nhiêu là nhiễu thật, bao nhiêu là vấn đề hợp lệ mà người bỏ sót?
- **RQ3 — So sánh mô hình:** Claude, GPT và DeepSeek khác nhau thế nào ở RQ1 và RQ2?
- (Phụ, nếu kịp) Loại vấn đề nào AI hay bắt được hoặc hay bỏ sót (logic, đặt tên, hiệu năng, kiểm thử, phong cách…)?

## 2. Mục tiêu, không phải mục tiêu, phản mục tiêu

**Mục tiêu**
- Một quy trình thực nghiệm chạy lại được: thu thập PR → AI review → so khớp → tính chỉ số.
- Số liệu cho 3 mô hình trên cùng một bộ PR, có bảng và biểu đồ.
- Báo cáo (tiếng Việt), slide trình bày và mã nguồn.

**Không phải mục tiêu**
- Xây dựng công cụ review hay bot GitHub để dùng thật.
- Tối ưu prompt cho từng mô hình: cả 3 dùng chung một prompt để so sánh công bằng.
- Đánh giá các công cụ thương mại (CodeRabbit, Copilot review). Chỉ nhắc tới trong phần tổng quan, vì chúng không chạy được trên PR cũ.

**Phản mục tiêu** (chạy được nhưng vẫn coi là thất bại)
- Con số trùng khớp được tính bởi LLM giám khảo mà không có kiểm chứng bằng tay.
- Mặc định coi mọi góp ý AI không khớp với người là "nhiễu", trong khi có thể đó là lỗi người bỏ sót.
- Cho AI review phiên bản code *sau khi* đã sửa theo review (rò rỉ đáp án).
- Không kịp hạn vì làm quy mô quá lớn.

## 3. Ràng buộc

- Thời gian ~2 tuần, một người thực hiện.
- Có sẵn API key: Claude, OpenAI, DeepSeek. Chi phí dự kiến vài USD.
- Ngôn ngữ của dự án được review: **Java** (người thực hiện chọn).
- Script thực nghiệm viết bằng **Python** (lựa chọn của Claude: hệ sinh thái gọi API và xử lý dữ liệu thuận tiện, và không ảnh hưởng tới việc code được review là Java).
- Lấy dữ liệu qua GitHub REST API (cần GitHub token cá nhân, quyền đọc public là đủ).

## 4. Các hướng đã cân nhắc

| Hướng | Mô tả | Đánh đổi | Kết quả |
|---|---|---|---|
| A. Tự thu thập PR thật | Lấy PR Java đã merge từ GitHub, kèm nhận xét review gắn vào dòng code | Bám sát đề, thực tế; tốn 1–2 ngày lọc dữ liệu | **Đã chọn** |
| B. Bộ dữ liệu có sẵn (CodeReviewer, Microsoft) | Cặp đoạn diff + một nhận xét | Nhanh nhưng không phải PR thật; nguy cơ mô hình đã thấy dữ liệu khi huấn luyện | Loại |
| C. Công cụ chuyên dụng (CodeRabbit, Copilot) | Fork repo, tạo lại PR để bot review | Đúng chất "công cụ" nhưng quá tốn thời gian trong 2 tuần | Loại |

Về công cụ AI: gọi trực tiếp LLM qua API, so sánh 3 mô hình (Claude, GPT, DeepSeek) với cùng một prompt.

## 5. Thiết kế thực nghiệm

### 5.1 Chọn dự án và PR
- 1–2 dự án Java mã nguồn mở có văn hóa review trên GitHub mạnh. Ứng viên: `apache/kafka`, `apache/commons-lang`, `apache/dubbo`, `junit-team/junit5`. Chọn cuối cùng sau khi xem dữ liệu thực tế.
- Tiêu chí lọc PR:
  - Đã merge.
  - Diff vừa phải (khoảng ≤ 500 dòng thay đổi, chủ yếu file `.java`).
  - Có ≥ 2 nhận xét review "có thể hành động" (xem 5.2).
- Quy mô: **30–50 PR**.

### 5.2 Làm sạch nhận xét của người (đáp án)
- Chỉ lấy nhận xét review gắn vào dòng code (review comments), do người khác tác giả PR viết.
- Loại bot, loại các trả lời trong thread (chỉ giữ nhận xét gốc), loại nhận xét không mang nội dung ("LGTM", "thanks", "+1").
- Một LLM phân loại từng nhận xét: *có thể hành động* hay *không*. Người thực hiện kiểm tra nhanh kết quả phân loại.
- **Chống rò rỉ đáp án:** AI review diff tại đúng commit mà người đã review (`original_commit_id` của nhận xét), không dùng diff cuối cùng khi merge.

### 5.3 AI review
- Đầu vào: tiêu đề và mô tả PR, cùng diff tại commit được review.
- Đầu ra: JSON dạng danh sách `{file, line, comment, category}`.
- Một prompt dùng chung cho 3 mô hình, temperature 0, chạy 1 lần mỗi PR.

### 5.4 So khớp (LLM giám khảo)
- Với mỗi PR: đưa danh sách nhận xét của người và danh sách góp ý của AI cho giám khảo. Giám khảo xác định các cặp *cùng nói về một vấn đề* (gần vị trí, cùng nội dung; không cần cùng câu chữ).
- Mô hình giám khảo: Claude. Ghi rõ trong báo cáo rủi ro thiên vị khi Claude chấm chính Claude.
- **Kiểm chứng bằng tay:** người thực hiện tự chấm khoảng 20% số PR, so với giám khảo và tính độ đồng thuận (Cohen's kappa).

### 5.5 Chỉ số
- **Tỉ lệ trùng khớp (recall)** = số nhận xét người được AI khớp / tổng nhận xét người.
- **Tỉ lệ không khớp** = số góp ý AI không khớp / tổng góp ý AI.
- **Phân tích nhiễu:** lấy mẫu các góp ý AI không khớp, chấm tay thành *nhiễu thật* hoặc *hợp lệ nhưng người bỏ sót*. Từ đó tính tỉ lệ cảnh báo nhiễu đã hiệu chỉnh.
- Báo cáo theo từng mô hình; nếu kịp thì thêm theo loại vấn đề.

### 5.6 Các quyết định chi tiết (Claude chốt, người thực hiện có thể sửa)

1. **Diff được review:** mỗi PR chạy AI **một lần**, trên diff của *toàn bộ PR* (từ merge-base tới commit được review) tại commit của **vòng review đầu tiên**. Đáp án chỉ giữ các nhận xét người viết trên chính commit đó; nhận xét ở các vòng sau bị loại. Ghi số lượng bị loại trong báo cáo.
2. **Phạm vi file:** chỉ file `.java`, áp dụng cho cả đầu vào AI lẫn đáp án. Bỏ nhận xét nằm ở phía code cũ (LEFT) của diff.
3. **Thế nào là "có thể hành động":** tính cả câu hỏi ngụ ý một vấn đề ("sao không dùng X?") và nhận xét về phong cách hay nitpick. Không tính câu hỏi chỉ để hiểu, lời khen, "LGTM". Chỉ số được báo cáo hai lần: *tất cả* và *bỏ nitpick/phong cách*.
4. **Danh mục loại vấn đề** (cố định trong prompt từ trước khi chạy): logic/bug, xử lý lỗi/ngoại lệ, hiệu năng, thiết kế/khả năng bảo trì, kiểm thử, phong cách/đặt tên/tài liệu. LLM gán loại cho nhận xét của người; người thực hiện kiểm tra nhanh.
5. **Kiểm chứng giám khảo:** đơn vị chấm là (a) từng nhận xét người — khớp hay không, và (b) từng góp ý AI — khớp hay không. Ngưỡng **kappa ≥ 0,6**. Nếu thấp hơn: sửa prompt giám khảo **một lần**, chạy lại, rồi báo cáo con số cuối cùng dù đạt hay không. Dành sẵn nửa ngày dự phòng cho việc này.
6. **Mô hình và tính tất định:** ưu tiên bản mạnh hiện hành *có hỗ trợ* temperature 0, tắt chế độ suy luận mở rộng nếu có. Nếu mô hình không cho đặt temperature thì dùng mặc định và ghi vào phần mối đe dọa đến tính hợp lệ. Mỗi PR chạy 1 lần. Lưu toàn bộ phản hồi thô để chạy lại được.
7. **Mẫu phân tích nhiễu:** 30 góp ý không khớp **cho mỗi mô hình** (90 mục), lấy ngẫu nhiên có seed cố định. RQ3 trả lời bằng thống kê mô tả kèm khoảng tin cậy bootstrap 95%, không làm kiểm định giả thuyết vì cỡ mẫu nhỏ.

## 6. Sản phẩm nộp

- Báo cáo tiếng Việt: tổng quan, phương pháp, kết quả, thảo luận, các mối đe dọa đến tính hợp lệ.
- Slide trình bày.
- Mã nguồn quy trình và dữ liệu đã thu thập (để chạy lại được).

## 7. Lịch dự kiến (đề xuất)

| Ngày | Việc |
|---|---|
| 1–3 | Chọn repo, thu thập và lọc PR, làm sạch nhận xét người |
| 4–5 | Script AI review, chạy cho 3 mô hình |
| 6–7 | So khớp bằng giám khảo, chấm tay kiểm chứng |
| 8–9 | Tính chỉ số, phân tích nhiễu, biểu đồ |
| 10–13 | Viết báo cáo, làm slide |
| 14 | Dự phòng |

## 8. Phần để người xây dựng tự quyết

- Chọn repo cụ thể trong danh sách ứng viên, và ngưỡng lọc chính xác.
- Cấu trúc thư mục và tên file của script.
- Câu chữ của prompt review và prompt giám khảo (miễn là dùng chung cho cả 3 mô hình).
- Phiên bản mô hình cụ thể của mỗi hãng (chọn bản mạnh hiện hành và ghi rõ trong báo cáo).
