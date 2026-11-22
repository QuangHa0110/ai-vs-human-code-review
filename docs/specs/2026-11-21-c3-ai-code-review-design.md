# Thiết kế đề tài C3 — Review mã tự động bằng AI so với người review

Cập nhật: 2026-11-22 (bản 3, theo hướng A) · Nhóm: 5 người · Không ràng buộc thời gian: ưu tiên chất lượng.

Tên báo cáo đề xuất: *AI review mã so với người review: nghiên cứu thực nghiệm trên pull request của các dự án Java mã nguồn mở.*

## 1. Mục đích và lý do

Đề tài C3, Chuyên đề 1, thạc sỹ Khoa học Máy tính. Yêu cầu của đề: *chạy công cụ AI review trên các pull request đã có nhận xét của người; đo mức trùng khớp và tỉ lệ cảnh báo nhiễu.* Sản phẩm là **báo cáo môn học đạt chuẩn nghiên cứu thực nghiệm**, không nhắm gửi hội nghị.

**Định vị** (chi tiết: [related-work.md](../related-work.md)). SWE-PRBench và SWR-Bench đã đo AI so với nhận xét người, chủ yếu trên Python, coi nhận xét người là đáp án tuyệt đối và không đo người. AACR-Bench gộp nhận xét người với góp ý AI đã được chuyên gia xác minh, nhưng không phân tích theo góc "người so với AI". Đề tài này:
1. Làm trên **Java**, có giả thuyết riêng cho Java (mục 5.9).
2. **Đo cả hai phía:** độ phủ và độ chính xác của người review *và* của AI trên cùng một tập vấn đề hợp lệ, dựa trên nhãn chuyên gia (AACR-Bench).
3. **Phân rã nhiễu:** góp ý AI không khớp là nhiễu thật hay lỗi người bỏ sót.
4. **Thống kê có ghép cặp**, đo độ dao động giữa các lần chạy, và **kiểm chứng giám khảo LLM** bằng nhãn chuyên gia và nhãn của nhóm.

## 2. Câu hỏi nghiên cứu

Trọng tâm (đúng yêu cầu đề):
- **RQ1 — Trùng khớp:** AI phát hiện lại được bao nhiêu phần trăm vấn đề người review đã nêu?
- **RQ2 — Nhiễu:** Bao nhiêu phần trăm góp ý của AI là nhiễu, và trong số góp ý không khớp với người, bao nhiêu là lỗi hợp lệ người bỏ sót?

Mở rộng:
- **RQ3 — So sánh mô hình:** Claude, GPT, DeepSeek khác nhau có ý nghĩa thống kê không, khi đã tính độ dao động giữa các lần chạy?
- **RQ4 — Loại vấn đề:** AI mạnh/yếu ở loại vấn đề nào so với người?
- **RQ5 — Người so với AI:** Trên tập vấn đề hợp lệ đã gộp, độ phủ và độ chính xác của người review so với từng mô hình ra sao?
- **RQ6 — Ngữ cảnh:** Cho AI xem thêm toàn bộ file (ngoài diff) làm tăng hay giảm trùng khớp và nhiễu?

Thứ tự ưu tiên: RQ1, RQ2 là bắt buộc; nếu phải cắt, cắt từ RQ6 ngược lên.

## 3. Mục tiêu, không phải mục tiêu, phản mục tiêu

**Mục tiêu:** pipeline chạy lại được; kết quả cho 3 mô hình trên 3 bộ dữ liệu (mục 5.1); báo cáo tiếng Việt cấu trúc bài báo, slide, gói tái lập (mã, dữ liệu, nhãn, hướng dẫn chấm, kế hoạch phân tích đăng ký trước).

**Không phải mục tiêu:** xây công cụ review dùng thật; tối ưu prompt riêng cho từng mô hình; đánh giá công cụ thương mại (CodeRabbit, Copilot); viết theo mẫu hội nghị.

**Phản mục tiêu:**
- Kết luận dựa trên giám khảo LLM chưa được kiểm chứng.
- Nhãn của nhóm không được đối chiếu với nhãn chuyên gia hay không đo đồng thuận.
- Coi mọi góp ý không khớp là nhiễu.
- AI thấy code sau khi đã sửa theo review (rò rỉ đáp án).
- Khẳng định "X tốt hơn Y" khi chênh lệch nằm trong độ dao động giữa các lần chạy hoặc không có ý nghĩa thống kê.
- Chỉnh phân tích sau khi đã thấy kết quả mà không ghi rõ là phân tích khám phá.
- Thổi phồng: trình bày độ phủ tương đối (trên tập gộp) như độ phủ tuyệt đối.

## 4. Ràng buộc

- Ngôn ngữ dự án được review: **Java**. Script: **Python** (lựa chọn của Claude).
- API key Claude, OpenAI, DeepSeek, GitHub token: chỉ trong `.env`, không commit. Repo công khai: `QuangHa0110/ai-vs-human-code-review`.
- Dữ liệu bên ngoài dùng theo giấy phép: AACR-Bench (Apache-2.0), SWE-PRBench (MIT).
- Dữ liệu công bố trong repo phải **ẩn danh tên người dùng GitHub** của người review (thay bằng mã).
- Tiêu chí chấm của giảng viên (mục 8) là thước đo thành công.

## 5. Thiết kế thực nghiệm

### 5.1 Ba bộ dữ liệu

| Mã | Nguồn | Vai trò | Nhãn |
|---|---|---|---|
| **D1** | AACR-Bench v1, phần Java: 30 PR, 5 repo (elasticsearch, keycloak, dbeaver, kestra, spring-ai-alibaba); 213 vấn đề hợp lệ (61 gốc từ người, 152 do AI tìm và chuyên gia xác minh); 59 nhận xét trong tập "negative" (16 gốc từ người, 43 từ AI) | RQ2, RQ4, **RQ5** (chính), RQ1 (phụ) | Chuyên gia (80 kỹ sư, ≥ 2 người/mục) |
| **D2** | Tự thu thập: PR Java đã merge từ 3–5 dự án mã nguồn mở, **tạo sau mốc dữ liệu huấn luyện** của cả 3 mô hình | **RQ1** (chính, quy mô), RQ2, RQ3, RQ4, RQ6 | Nhận xét người làm đáp án; nhóm chấm tay phần cần thiết |
| **D3** | SWE-PRBench: 15 PR Java + một mẫu PR Python | Hiệu chỉnh pipeline: so recall của nhóm với số đã công bố | Nhận xét người (theo cách của SWE-PRBench) |

**Phải xác minh trước khi dùng D1** (việc đầu tiên của thực nghiệm): ý nghĩa chính xác của tập "negative" (có phải nhận xét bị chuyên gia loại không), cách chuyên gia xác minh, và cách lấy diff tại `source_commit`/`target_commit`. Nếu "negative" không phải nhãn loại của chuyên gia thì RQ5 chỉ đo độ phủ, không đo precision của người, và RQ2 trên D1 dựa vào nhãn của nhóm.

**Cỡ mẫu D2:** chốt bằng phân tích lực thống kê trong kế hoạch đăng ký trước: đủ để McNemar ghép cặp phát hiện chênh lệch recall **10 điểm phần trăm** giữa hai mô hình với α = 0,05 (sau Holm) và lực 0,8. Dự kiến vài trăm PR. Lấy mẫu ngẫu nhiên phân tầng theo dự án, seed cố định `20261121`.

### 5.2 Làm sạch nhận xét người (D2)
- Chỉ nhận xét gắn dòng code, của người khác tác giả PR; loại bot, trả lời trong thread, "LGTM"/"thanks".
- Tiêu chí "thực chất" theo SWE-PRBench: ≥ 10 từ, có tham chiếu code hoặc từ khóa nội dung; loại PR có > 30% nhận xét từ bot.
- LLM phân loại "có thể hành động" và loại vấn đề; nhóm kiểm tra một mẫu.
- PR merged, ≤ 500 dòng thay đổi trong `.java`, ≥ 2 nhận xét có thể hành động.

### 5.3 AI review
- 3 mô hình: bản mạnh hiện hành của Claude, GPT, DeepSeek; ghi tên, phiên bản, ngày chạy. Temperature 0 nếu hỗ trợ.
- Một prompt chung, đầu ra JSON `{file, line, comment, category}`.
- **Chạy 3 lần** mỗi (PR, mô hình) để đo độ dao động.
- Hai cấu hình ngữ cảnh (RQ6): **diff** (chính) và **diff + toàn bộ file đã sửa**.
- Chống rò rỉ: D2 dùng diff từ merge-base tới commit của **vòng review đầu tiên**, chỉ giữ nhận xét người trên commit đó; D1 dùng diff theo commit của AACR-Bench.

### 5.4 Giám khảo so khớp
- Ghép góp ý AI với vấn đề tham chiếu (nhận xét người ở D2; vấn đề hợp lệ của chuyên gia ở D1): "cùng nói về một vấn đề, gần vị trí".
- Giám khảo chính Claude; giám khảo thứ hai GPT trên tập kiểm chứng. Đo đồng thuận giữa hai giám khảo và với nhãn người; kiểm tra thiên vị "chấm họ nhà mình" bằng cách so độ chính xác theo từng mô hình được chấm.
- **Hiệu chỉnh trên D3:** nếu recall của pipeline trên D3 lệch xa số SWE-PRBench công bố, tìm nguyên nhân trước khi chạy D1, D2.

### 5.5 Chấm tay của nhóm
Nhóm chấm ba việc: (a) góp ý *hợp lệ / nhiễu / không đủ thông tin*, nếu nhiễu thì nguyên nhân theo bảng của SWR-Bench; (b) *có khớp không* (kiểm chứng giám khảo); (c) *gộp trùng* các góp ý hợp lệ cùng một vấn đề.

- **"Hợp lệ"** = một người review giỏi của dự án này muốn vấn đề được nêu ra (không chỉ "đúng kỹ thuật").
- **Hiệu chuẩn với chuyên gia:** trước khi chấm dữ liệu mới, mỗi người chấm một tập mục đã có nhãn chuyên gia của AACR-Bench (cả hợp lệ lẫn negative, ẩn nhãn). Đồng thuận người chấm–chuyên gia phải đạt **kappa ≥ 0,6**; chưa đạt thì sửa hướng dẫn và chấm lại tập hiệu chuẩn mới. Kết quả này được báo cáo, là bằng chứng nhóm đủ năng lực gán nhãn.
- Mỗi mục **2 người chấm độc lập**, người thứ ba phân xử; báo cáo kappa người–người. Ẩn nguồn (mô hình nào / người hay AI).
- Người chấm xem diff, toàn bộ thread PR và code sau khi sửa.
- Chấm **toàn bộ** góp ý AI không khớp trên D1 (mọi mô hình, lần chạy 1) và một mẫu ngẫu nhiên trên D2 (cỡ mẫu chốt trong kế hoạch đăng ký trước).

### 5.6 Tập vấn đề gộp và baseline của người (RQ5, trên D1)
- **V** = vấn đề hợp lệ của AACR-Bench ∪ vấn đề hợp lệ mới mà 3 mô hình của nhóm tìm ra (nhóm chấm, gộp trùng).
- Với mỗi vấn đề trong V: cột "tìm thấy / không" cho người review gốc và cho từng mô hình. Độ phủ = số tìm thấy / |V|.
- Precision của người = nhận xét gốc từ người được giữ / tổng nhận xét gốc từ người (dùng tập negative, nếu xác minh được ý nghĩa ở 5.1).
- Gọi đúng tên: **độ phủ tương đối** (pooled recall). Vấn đề không ai tìm ra không nằm trong V.
- Kiểm định: Cochran's Q trên 4 bên (người + 3 mô hình), McNemar từng cặp người–mô hình, hiệu chỉnh Holm.

### 5.7 Chỉ số và kiểm định
- RQ1: recall = nhận xét người được AI khớp / tổng nhận xét người; báo cáo bản *tất cả* và *bỏ phong cách/đặt tên/tài liệu*.
- RQ2: tỉ lệ không khớp; tỉ lệ nhiễu thật; tỉ lệ "người bỏ sót"; precision hiệu chỉnh.
- Khoảng tin cậy 95% bằng bootstrap **theo PR**.
- RQ3: Cochran's Q rồi McNemar từng cặp, Holm, odds ratio; độ dao động giữa 3 lần chạy báo cáo riêng (tỉ lệ góp ý ổn định, độ lệch chuẩn recall), và kết luận chỉ khi chênh lệch giữa mô hình vượt độ dao động này.
- RQ6: so cấu hình ghép cặp trên cùng PR (McNemar cho recall; bootstrap cho tỉ lệ nhiễu).

### 5.8 Quy trình nghiên cứu
1. Xác minh D1; dựng pipeline; hiệu chỉnh trên D3.
2. **Pilot** ~10 PR của D2: chỉnh prompt review, prompt giám khảo, hướng dẫn chấm. Sau đó **đóng băng**.
3. **Đăng ký trước kế hoạch phân tích:** giả thuyết, chỉ số, kiểm định, cỡ mẫu, quy tắc loại dữ liệu — commit vào repo *trước* khi chạy chính. Mọi phân tích ngoài kế hoạch ghi là "khám phá".
4. Hiệu chuẩn người chấm với chuyên gia (5.5).
5. Chạy chính trên D1, D2; giám khảo; chấm tay; phân xử.
6. Phân tích theo kế hoạch; viết báo cáo.

### 5.9 Giả thuyết riêng cho Java (đăng ký trước)
- H1: Recall của AI cao hơn ở `error_handling` và `logic_bug` (null, ngoại lệ, tài nguyên) so với `design_maintainability`.
- H2: Người review nêu tỉ lệ vấn đề thiết kế cao hơn AI; AI nêu tỉ lệ vấn đề phong cách và kiểm thử cao hơn người (đối chiếu với nhận xét tương tự của c-CRAB trên Python).
- H3: Thêm toàn bộ file làm tăng số góp ý và tỉ lệ nhiễu, không làm tăng recall (đối chiếu SWE-PRBench).

### 5.10 Các quyết định chi tiết (Claude chốt, nhóm có thể sửa)
1. Danh mục loại (cố định trước khi chạy): `logic_bug`, `error_handling`, `performance`, `design_maintainability`, `testing`, `style_naming_docs`; ánh xạ sang 4 loại của AACR-Bench khi so sánh.
2. Chỉ file `.java`; bỏ nhận xét ở phía code cũ.
3. "Có thể hành động" gồm cả câu hỏi ngụ ý vấn đề và nitpick; không gồm câu hỏi chỉ để hiểu, lời khen.
4. Ngưỡng kappa ≥ 0,6 cho: người chấm–chuyên gia, người–người, giám khảo–người.
5. Phân tích chính dùng lần chạy 1; lần 2–3 chỉ dùng cho độ dao động. Hợp các lần chạy báo cáo phụ.
6. **Khi D2 không đủ PR theo phân tích lực**, nới lần lượt: (a) thêm dự án Java (tối đa ~10); (b) lùi mốc thời gian về sau mốc huấn luyện *sớm nhất* trong 3 mô hình, báo cáo riêng phần PR có thể đã bị mô hình thấy; (c) chấp nhận lực thấp hơn và ghi rõ chênh lệch nhỏ nhất phát hiện được. Không nới tiêu chí lọc nhận xét và PR (5.2).
7. **Khi giám khảo LLM đạt kappa < 0,6 với người** sau một lần sửa prompt: không chuyển sang so khớp thủ công toàn bộ. Kết quả dựa trên giám khảo được ghi là *thăm dò*; trên tập kiểm chứng, nhãn so khớp của người được dùng làm kết quả chính và báo cáo cạnh kết quả của giám khảo.
8. **Hiệu chỉnh trên D3** dùng thêm **một mô hình mà SWE-PRBench đã đánh giá** (ưu tiên Claude Sonnet 4.6 hoặc GPT-4o nếu còn gọi được), chỉ trên các PR thuộc tập đánh giá của họ. Đạt khi recall của nhóm nằm trong khoảng tin cậy họ công bố. Không đạt thì tìm nguyên nhân và ghi vào báo cáo, **không chặn** việc chạy D1, D2.

### 5.11 Mối đe dọa đến tính hợp lệ
- *Nội tại:* thiên vị giám khảo LLM; chỉ dùng vòng review đầu; người chấm không phải chuyên gia dự án (giảm bằng hiệu chuẩn với chuyên gia).
- *Cấu trúc:* "hợp lệ" mang tính chủ quan; độ phủ trên V là tương đối; tập V của D1 thiên về những gì 6 mô hình cũ của AACR-Bench tìm được.
- *Ngoại tại:* chỉ Java, số dự án hạn chế, một prompt.
- *Tái lập:* mô hình thương mại thay đổi; D1 có PR từ 12/2024–12/2025 nên có thể đã nằm trong dữ liệu huấn luyện (D2 chọn PR mới để đối chứng).
- *So sánh với nghiên cứu trước:* khác dữ liệu, cách lọc, giám khảo; so định tính, trừ D3.

## 6. Tổ chức nhóm

| Vai trò | Phụ trách | Phần báo cáo |
|---|---|---|
| Dữ liệu | D1 (xác minh), D2 (thu thập, làm sạch), D3 | Dữ liệu |
| Pipeline AI | Prompt, review 3 mô hình × 2 cấu hình × 3 lần, giám khảo | Thiết lập thực nghiệm |
| Chấm tay | Hướng dẫn chấm, hiệu chuẩn, chia việc, phân xử, kappa | Quy trình gán nhãn |
| Thống kê | Phân tích lực, kế hoạch đăng ký trước, kiểm định, biểu đồ | Kết quả |
| Nghiên cứu liên quan | Tổng quan, đối chiếu số liệu gốc, giả thuyết Java | Giới thiệu, Liên quan |

Cả 5 người: chấm tay, viết Thảo luận và Mối đe dọa, **đánh giá chéo** công việc của nhau. Mỗi người phải giải thích được toàn bộ pipeline (yêu cầu "mọi thành viên trả lời được"). Nhóm tự phân công người cụ thể.

## 7. Sản phẩm nộp

Báo cáo tiếng Việt (tóm tắt, giới thiệu, liên quan, câu hỏi nghiên cứu, phương pháp, kết quả, thảo luận, mối đe dọa, kết luận, tài liệu tham khảo); slide; gói tái lập (mã, danh sách PR + commit, đầu ra thô, nhãn đã ẩn danh, hướng dẫn chấm, kế hoạch đăng ký trước, README).

## 8. Đối chiếu với tiêu chí chấm của giảng viên

| Tiêu chí (trọng số) | Thiết kế đáp ứng bằng |
|---|---|
| Hiểu vấn đề, tổng quan (20%) | RQ rõ; tổng quan cập nhật 2026; khoảng trống cụ thể (mục 1) |
| Phương pháp, thiết kế thực nghiệm (20%) | Nhãn chuyên gia (D1), giám khảo được kiểm chứng, hiệu chỉnh D3, đăng ký trước, gói tái lập |
| Kết quả, phân tích phản biện (25%) | Khoảng tin cậy, kiểm định, độ dao động, mục 5.11; gọi đúng "độ phủ tương đối" |
| Báo cáo viết (10%) | Cấu trúc bài báo |
| Thuyết trình, trả lời câu hỏi (15%) | Mỗi người hiểu toàn bộ pipeline |
| Làm việc nhóm (10%) | Phân vai, chấm tay chéo, đánh giá chéo |

## 9. Phần để người xây dựng tự quyết

Dự án cụ thể của D2; câu chữ prompt và hướng dẫn chấm (sau pilot thì đóng băng); phiên bản mô hình cụ thể; cấu trúc mã nguồn; công cụ chấm tay; cỡ tập hiệu chuẩn chuyên gia (đủ để ước lượng kappa, gợi ý ≥ 50 mục).
