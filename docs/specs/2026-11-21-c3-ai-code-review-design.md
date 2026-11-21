# Thiết kế đề tài C3 — Review mã tự động bằng AI so với người review

Ngày: 2026-11-21 · Thời hạn: ~2 tuần (đến khoảng 2026-12-05) · Nhóm: 5 người

## 1. Mục đích và lý do

Đề tài C3 thuộc Chuyên đề 1 của chương trình thạc sỹ Khoa học Máy tính. Yêu cầu của đề: *chạy công cụ AI review trên các pull request đã có nhận xét của người; đo mức trùng khớp và tỉ lệ cảnh báo nhiễu.*

Sản phẩm là **báo cáo môn học đạt chuẩn một nghiên cứu thực nghiệm**: có câu hỏi nghiên cứu, phương pháp chặt chẽ, có kiểm chứng, có thống kê, và người khác chạy lại được. Nhóm **không** nhắm gửi bài hội nghị, nên không cần khảo sát tài liệu toàn diện hay chứng minh đóng góp mới so với mọi nghiên cứu trước.

Câu hỏi nghiên cứu:
- **RQ1 — Trùng khớp:** Trong các vấn đề mà người review đã chỉ ra, AI phát hiện lại được bao nhiêu phần trăm?
- **RQ2 — Nhiễu:** Trong các góp ý của AI, bao nhiêu phần trăm không tương ứng với nhận xét nào của người, và trong số đó bao nhiêu là nhiễu thật, bao nhiêu là vấn đề hợp lệ mà người bỏ sót?
- **RQ3 — So sánh mô hình:** Claude, GPT và DeepSeek có khác nhau có ý nghĩa thống kê ở RQ1 và RQ2 không?
- **RQ4 — Loại vấn đề:** AI bắt tốt và bỏ sót những loại vấn đề nào (logic, xử lý lỗi, hiệu năng, thiết kế, kiểm thử, phong cách)?

## 2. Mục tiêu, không phải mục tiêu, phản mục tiêu

**Mục tiêu**
- Một quy trình thực nghiệm chạy lại được: thu thập PR → AI review → so khớp → tính chỉ số → kiểm định.
- Số liệu cho 3 mô hình trên cùng một bộ PR, có bảng, biểu đồ và kiểm định thống kê.
- Báo cáo theo cấu trúc bài báo khoa học (tiếng Việt), slide, và gói tái lập (mã nguồn, dữ liệu, nhãn chấm tay).

**Không phải mục tiêu**
- Xây dựng công cụ review hay bot GitHub để dùng thật.
- Tối ưu prompt riêng cho từng mô hình: cả 3 dùng chung một prompt để so sánh công bằng.
- Đánh giá các công cụ thương mại (CodeRabbit, Copilot review). Chỉ nhắc trong phần nghiên cứu liên quan, vì chúng không chạy được trên PR cũ.
- Viết bài theo mẫu hội nghị hay bằng tiếng Anh.

**Phản mục tiêu** (chạy được nhưng vẫn coi là thất bại)
- Con số trùng khớp do LLM giám khảo tính mà không được kiểm chứng bằng nhãn của người.
- Nhãn chấm tay chỉ do một người làm, không đo độ đồng thuận.
- Mặc định coi mọi góp ý AI không khớp là "nhiễu", trong khi có thể đó là lỗi người bỏ sót.
- Cho AI review phiên bản code *sau khi* đã sửa theo review (rò rỉ đáp án).
- Khẳng định "mô hình X tốt hơn" mà không có kiểm định thống kê.
- Không kịp hạn vì làm quy mô quá lớn.

## 3. Ràng buộc

- Thời gian ~2 tuần, nhóm 5 người.
- Có sẵn API key: Claude, OpenAI, DeepSeek. Chi phí dự kiến dưới 20 USD.
- Ngôn ngữ của dự án được review: **Java** (nhóm chọn).
- Script thực nghiệm viết bằng **Python** (lựa chọn của Claude: hệ sinh thái gọi API và thống kê thuận tiện; không ảnh hưởng tới việc code được review là Java).
- Lấy dữ liệu qua GitHub REST API (cần GitHub token cá nhân, quyền đọc public).
- Mã nguồn và dữ liệu nằm trong repo `QuangHa0110/ai-vs-human-code-review`. Khóa API để trong `.env`, không commit.

## 4. Các hướng đã cân nhắc

| Hướng | Mô tả | Đánh đổi | Kết quả |
|---|---|---|---|
| A. Tự thu thập PR thật | Lấy PR Java đã merge từ GitHub, kèm nhận xét review gắn vào dòng code | Bám sát đề, thực tế; tốn 1–2 ngày lọc dữ liệu | **Đã chọn** |
| B. Bộ dữ liệu có sẵn (CodeReviewer, Microsoft) | Cặp đoạn diff + một nhận xét | Nhanh nhưng không phải PR thật; nguy cơ mô hình đã thấy dữ liệu khi huấn luyện | Loại |
| C. Công cụ chuyên dụng (CodeRabbit, Copilot) | Fork repo, tạo lại PR để bot review | Đúng chất "công cụ" nhưng quá tốn thời gian trong 2 tuần | Loại |

Về công cụ AI: gọi trực tiếp LLM qua API, so sánh 3 mô hình (Claude, GPT, DeepSeek) với cùng một prompt.

## 5. Thiết kế thực nghiệm

### 5.1 Chọn dự án và PR
- **2–3 dự án Java** mã nguồn mở có văn hóa review trên GitHub mạnh, khác nhau về lĩnh vực để kết quả khái quát hơn. Ứng viên: `apache/kafka`, `apache/commons-lang`, `apache/dubbo`, `junit-team/junit5`. Chọn cuối cùng sau khi xem dữ liệu thực tế.
- Tiêu chí lọc PR:
  - Đã merge.
  - Diff vừa phải (khoảng ≤ 500 dòng thay đổi trong file `.java`).
  - Có ≥ 2 nhận xét review "có thể hành động" (xem 5.2).
- Quy mô: **khoảng 100 PR**, chia tương đối đều giữa các dự án. Lấy mẫu ngẫu nhiên có seed cố định từ tập PR đạt tiêu chí, trong một khoảng thời gian xác định (ghi rõ trong báo cáo).

### 5.2 Làm sạch nhận xét của người (đáp án)
- Chỉ lấy nhận xét review gắn vào dòng code (review comments), do người khác tác giả PR viết.
- Loại bot, loại các trả lời trong thread (chỉ giữ nhận xét gốc), loại nhận xét không mang nội dung ("LGTM", "thanks", "+1").
- Một LLM phân loại từng nhận xét: *có thể hành động* hay *không*, và gán loại vấn đề (xem 5.6). Nhóm kiểm tra một mẫu kết quả phân loại.
- **Chống rò rỉ đáp án:** AI review diff tại đúng commit mà người đã review, không dùng diff cuối cùng khi merge.

### 5.3 AI review
- Đầu vào: tiêu đề và mô tả PR, cùng diff tại commit được review.
- Đầu ra: JSON dạng danh sách `{file, line, comment, category}`.
- Một prompt dùng chung cho 3 mô hình, chạy 1 lần mỗi PR, lưu toàn bộ phản hồi thô.

### 5.4 So khớp (LLM giám khảo)
- Với mỗi PR: đưa danh sách nhận xét của người và danh sách góp ý của AI cho giám khảo. Giám khảo xác định các cặp *cùng nói về một vấn đề* (gần vị trí, cùng nội dung; không cần cùng câu chữ).
- Mô hình giám khảo: Claude. Rủi ro thiên vị khi Claude chấm chính Claude được ghi trong phần mối đe dọa, và được kiểm tra bằng cách so độ chính xác của giám khảo trên đầu ra của từng mô hình riêng.

### 5.5 Chấm tay (nhãn chuẩn của người)
- **Hướng dẫn chấm** viết trước, có ví dụ cho từng trường hợp. Cả nhóm chấm thử 5 PR chung để thống nhất cách hiểu, sau đó mới chia việc.
- **Tập kiểm chứng giám khảo:** khoảng 20% số PR (~20 PR, cả 3 mô hình). Mỗi PR do **2 người chấm độc lập**, người thứ ba phân xử chỗ lệch.
  - Đo độ đồng thuận giữa 2 người chấm (Cohen's kappa) trước, rồi đo đồng thuận giữa nhãn đã phân xử và giám khảo.
  - Ngưỡng chấp nhận **kappa ≥ 0,6** cho cả hai. Nếu giám khảo dưới ngưỡng: sửa prompt giám khảo **một lần**, chạy lại, rồi báo cáo con số cuối cùng dù đạt hay không.
- **Phân tích nhiễu:** lấy ngẫu nhiên (seed cố định) **50 góp ý không khớp cho mỗi mô hình** (150 mục). Mỗi mục do 2 người chấm độc lập thành *nhiễu thật* hoặc *hợp lệ nhưng người bỏ sót*, người thứ ba phân xử. Báo cáo kappa.
- Người chấm không biết góp ý đến từ mô hình nào (ẩn tên mô hình) để tránh thiên kiến.

### 5.6 Các quyết định chi tiết (Claude chốt, nhóm có thể sửa)

1. **Diff được review:** mỗi PR chạy AI **một lần**, trên diff của *toàn bộ PR* (từ merge-base tới commit được review) tại commit của **vòng review đầu tiên**. Đáp án chỉ giữ các nhận xét người viết trên chính commit đó; nhận xét ở các vòng sau bị loại, và số lượng bị loại được ghi trong báo cáo.
2. **Phạm vi file:** chỉ file `.java`, cho cả đầu vào AI lẫn đáp án. Bỏ nhận xét nằm ở phía code cũ (LEFT) của diff.
3. **Thế nào là "có thể hành động":** tính cả câu hỏi ngụ ý một vấn đề ("sao không dùng X?") và nhận xét về phong cách hay nitpick. Không tính câu hỏi chỉ để hiểu, lời khen, "LGTM". Chỉ số được báo cáo hai lần: *tất cả* và *bỏ nitpick/phong cách*.
4. **Danh mục loại vấn đề** (cố định trong prompt từ trước khi chạy): logic/bug, xử lý lỗi/ngoại lệ, hiệu năng, thiết kế/khả năng bảo trì, kiểm thử, phong cách/đặt tên/tài liệu.
5. **Mô hình và tính tất định:** ưu tiên bản mạnh hiện hành *có hỗ trợ* temperature 0, tắt chế độ suy luận mở rộng nếu có. Nếu mô hình không cho đặt temperature thì dùng mặc định và ghi vào phần mối đe dọa. Ghi rõ tên và phiên bản mô hình, ngày chạy.

### 5.7 Chỉ số và kiểm định
- **Tỉ lệ trùng khớp (recall)** = số nhận xét người được AI khớp / tổng nhận xét người.
- **Tỉ lệ không khớp** = số góp ý AI không khớp / tổng góp ý AI.
- **Tỉ lệ nhiễu đã hiệu chỉnh** = tỉ lệ không khớp × tỉ lệ *nhiễu thật* trong mẫu chấm tay.
- **Độ chính xác hiệu chỉnh** (precision) = 1 − tỉ lệ nhiễu đã hiệu chỉnh.
- Mọi tỉ lệ kèm **khoảng tin cậy 95%** (bootstrap theo PR, vì các nhận xét trong cùng một PR không độc lập).
- **So sánh 3 mô hình (RQ3):** recall được so trên cùng tập nhận xét của người, nên dùng **Cochran's Q**, sau đó **McNemar** từng cặp với hiệu chỉnh Holm. Báo cáo độ lớn hiệu ứng (chênh lệch tỉ lệ, odds ratio).
- **Theo loại vấn đề (RQ4):** bảng recall theo từng loại và từng mô hình. Chỉ mô tả, không kiểm định, vì nhiều nhóm sẽ có cỡ mẫu nhỏ.

### 5.8 Mối đe dọa đến tính hợp lệ (phải có trong báo cáo)
- *Nội tại:* thiên vị của giám khảo LLM; lựa chọn chỉ dùng vòng review đầu; người review có thể đã bỏ sót vấn đề.
- *Cấu trúc:* nhận xét của người là "đáp án" không hoàn hảo; định nghĩa "trùng khớp" mang tính chủ quan.
- *Ngoại tại:* chỉ Java, chỉ 2–3 dự án Apache/mã nguồn mở, một prompt duy nhất.
- *Tái lập:* mô hình thương mại thay đổi theo thời gian; có thể dữ liệu PR công khai đã nằm trong dữ liệu huấn luyện.

## 6. Tổ chức nhóm (đề xuất)

| Vai trò | Việc chính |
|---|---|
| Dữ liệu | Chọn dự án, thu thập và lọc PR, làm sạch nhận xét người |
| Pipeline | Script AI review cho 3 mô hình, script giám khảo |
| Chấm tay | Viết hướng dẫn chấm, điều phối chia việc và phân xử (cả 5 người cùng chấm) |
| Thống kê | Tính chỉ số, khoảng tin cậy, kiểm định, biểu đồ |
| Nghiên cứu liên quan | Tổng quan các nghiên cứu về AI code review và LLM-as-judge |

Mỗi người còn viết một phần báo cáo gắn với vai trò của mình. Nhóm tự phân công người cụ thể.

## 7. Sản phẩm nộp

- **Báo cáo tiếng Việt theo cấu trúc bài báo:** tóm tắt, giới thiệu, nghiên cứu liên quan, câu hỏi nghiên cứu, phương pháp, kết quả, thảo luận, mối đe dọa đến tính hợp lệ, kết luận, tài liệu tham khảo.
- Slide trình bày.
- **Gói tái lập:** mã nguồn, danh sách PR (kèm commit), đầu ra thô của mô hình, nhãn chấm tay, hướng dẫn chấm, và README hướng dẫn chạy lại.

## 8. Lịch dự kiến

| Ngày | Việc |
|---|---|
| 1–2 | Chọn dự án, thu thập PR; song song: viết bản nháp hướng dẫn chấm, bắt đầu tổng quan tài liệu |
| 3 | Làm sạch nhận xét người; chốt prompt review và danh mục loại vấn đề |
| 4 | Chạy AI review cho 3 mô hình; cả nhóm chấm thử 5 PR để thống nhất hướng dẫn |
| 5 | Chạy giám khảo |
| 6–8 | Chấm tay song song (tập kiểm chứng + mẫu nhiễu), phân xử |
| 9 | Tính kappa; nếu cần, sửa prompt giám khảo và chạy lại |
| 10 | Thống kê, kiểm định, biểu đồ |
| 11–13 | Viết báo cáo, làm slide, hoàn thiện gói tái lập |
| 14 | Dự phòng, rà soát chéo |

## 9. Phần để người xây dựng tự quyết

- Chọn dự án cụ thể trong danh sách ứng viên, và ngưỡng lọc chính xác.
- Cấu trúc thư mục và tên file của script.
- Câu chữ của prompt review, prompt giám khảo và hướng dẫn chấm (prompt review dùng chung cho cả 3 mô hình).
- Phiên bản mô hình cụ thể của mỗi hãng, theo nguyên tắc ở mục 5.6.
- Công cụ chấm tay (bảng tính chung hay công cụ đơn giản tự viết).
