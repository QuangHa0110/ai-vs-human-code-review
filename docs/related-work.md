# Ghi chú nghiên cứu liên quan

Tra cứu ngày 2026-11-21. Phần lớn là preprint arXiv, chưa qua bình duyệt (trừ khi ghi rõ). Các con số được lấy từ bản HTML trên arXiv. Một phần nội dung của SWE-PRBench chỉ đọc qua bản tóm tắt (đánh dấu ⚠), nên cần đối chiếu lại bảng gốc trước khi trích dẫn.

## 1. Hai nghiên cứu sát nhất với đề tài

### SWE-PRBench (Kumar, arXiv 2603.26130, 03/2026, preprint)
- **Câu hỏi:** AI review bắt lại được bao nhiêu vấn đề mà người review đã chỉ ra trên PR thật. Đây gần như đúng RQ1 của nhóm.
- **Dữ liệu:** 350 PR đã merge từ 65 repo; Python 69%, **Java chỉ 15 PR (4,3%)**. Phần đánh giá chỉ chạy trên 100 PR.
- **Đáp án:** nhận xét review của người, giữ nguyên văn. Lọc: ≥ 2 nhận xét "thực chất" (≥ 10 từ, có tham chiếu code), loại bot. Bài không mô tả cách xử lý review nhiều vòng.
- **Mô hình:** 8 mô hình, temperature 0, gồm Claude Haiku 4.5, Sonnet 4.6, GPT-4o, DeepSeek V3 và một số mô hình khác.
- **So khớp:** GPT-5.2 làm giám khảo, gán nhãn CONFIRMED / PLAUSIBLE / FABRICATED, kết hợp ghép cặp bằng embedding. Kappa 0,75 khi so với rubric (**không phải so với người**); kappa 0,616 khi so giữa hai giám khảo.
- **Kết quả:** các mô hình bắt được **15–31%** vấn đề người đã chỉ ra. Tỉ lệ bịa (fabricated) 19–42% ⚠. Bốn mô hình đứng đầu không khác nhau có ý nghĩa thống kê (bootstrap CI) ⚠. Đưa thêm ngữ cảnh vào thì recall lại *giảm*.
- **Chưa làm:** không đo baseline của người; **không kiểm tra liệu các góp ý PLAUSIBLE (không khớp) có phải lỗi thật mà người bỏ sót**; chỉ dùng một họ mô hình làm giám khảo.

### SWR-Bench (Zeng và cộng sự, ĐH Bắc Kinh, FSE 2026 / PACMSE, đã bình duyệt; arXiv 2509.01494)
- **Dữ liệu:** 1.000 PR (500 có thay đổi, 500 "sạch") từ 12 dự án **Python** thuộc SWE-Bench. **Không có Java.**
- **Đáp án:** không dùng nhận xét thô. Gemini-2.5-Pro đọc toàn bộ lịch sử review và rút ra các "change-action" (vị trí, vấn đề, cách sửa), sau đó 5 học viên cao học kiểm tra theo cặp, kappa khoảng 0,66. Các thay đổi tầm thường (tài liệu, định dạng) bị loại.
- **So khớp:** Gemini-2.5-Flash làm giám khảo theo kiểu "hit". Được kiểm chứng trên 100 báo cáo với 3 chuyên gia: kappa người–LLM tương đương kappa người–người (khoảng 0,71–0,87).
- **Kết quả:** precision rất thấp, hầu hết công cụ dưới 10%. Công cụ tốt nhất (PR-Review + Gemini-2.5-Pro) đạt P 16,7% / R 23,2% / F1 19,4%. Recall giảm từ 38% xuống 9% khi PR có nhiều vấn đề.
- **Phân loại góp ý sai (100 mẫu):** thiếu hiểu biết ngữ cảnh 48%, quá nhạy với thay đổi 17%, mơ hồ/không hành động được 16%, thiếu kiến thức miền 13%, ngược best practice 3%, khác 3%.
- **Chưa làm:** **không kiểm định thống kê, không có khoảng tin cậy**; mọi góp ý không khớp đều bị tính là sai, không kiểm tra liệu người có bỏ sót; chỉ có Python.

## 2. Nghiên cứu về nhiễu và phản ứng của lập trình viên
- **"Go Home Copilot, You're Drunk"** (arXiv 2607.21997): 54.713 nhận xét của Copilot, Cursor và Codex trên 341 repo Python. Copilot có tỉ lệ được xử lý 72,9%.
- **Nghiên cứu về CodeRabbit** (preprint 07/2026, theo kết quả tìm kiếm): 31.073 nhận xét; lập trình viên từ chối 56,3%. Trong số bị từ chối, 43,3% là cảnh báo sai.
- **From Industry Claims to Empirical Reality** (arXiv 2604.03196): trên dữ liệu AIDev, các PR chỉ do agent review có tỉ lệ merge 45,2%, so với 68,4% khi người review.
- **From Human-Centric to Agentic Code Review** (arXiv 2607.13196): 1,02 triệu PR. PR chỉ có người review ít "review smell" hơn đa số kiểu có AI tham gia.
- **Rethinking Code Review Workflows with LLM Assistance** (ESEM 2025, arXiv 2505.16339): thực nghiệm tại công ty WirelessCar; AI giúp review nhanh hơn với PR lớn hoặc khi người review chưa quen code.

## 3. Đánh giá bằng LLM giám khảo và chất lượng đáp án
- **MCR-Bench** (arXiv 2608.27442): "LLM-Hit-Judge" đạt QWK 0,70–0,73, tốt hơn chấm điểm bằng LLM thông thường.
- **DeepCRCEval** (arXiv 2412.18291): BLEU và các độ đo trùng chữ không phản ánh được chất lượng review; đề xuất chấm bằng người kết hợp LLM.
- **Automated Classification of Human Code Review Comments** (arXiv 2604.23667): chỉ khoảng 64% nhận xét trong bộ CodeReviewer là hợp lệ, nên việc làm sạch đáp án là bắt buộc.
- **CRScore** (NAACL 2025): độ đo dựa trên tham chiếu giả do LLM sinh; tương quan Spearman 0,54 với người.
- **LLM-as-a-Judge for SE** (arXiv 2510.24367): tổng quan về dùng LLM làm giám khảo trong kỹ thuật phần mềm.

## 4. Khoảng trống mà đề tài C3 có thể lấp
1. **Java gần như chưa được khảo sát:** cả hai benchmark chính đều chủ yếu là Python.
2. **Phân rã nhiễu (RQ2):** chưa nghiên cứu nào chấm tay xem góp ý "không khớp" là nhiễu thật hay lỗi người bỏ sót. Họ hoặc tính tất cả là sai (SWR-Bench), hoặc gắn nhãn PLAUSIBLE rồi bỏ qua (SWE-PRBench).
3. **Kiểm định thống kê có ghép cặp** giữa các mô hình: SWR-Bench không có; SWE-PRBench chỉ dùng bootstrap CI.
4. **Giám khảo được kiểm chứng với người và đổi chéo họ mô hình:** SWE-PRBench chỉ kiểm chứng với rubric.

## Nguồn
- https://arxiv.org/abs/2603.26130 — SWE-PRBench
- https://arxiv.org/abs/2509.01494 — SWR-Bench
- https://arxiv.org/abs/2607.21997 — Go Home Copilot
- https://arxiv.org/abs/2604.03196 — From Industry Claims to Empirical Reality
- https://arxiv.org/abs/2607.13196 — From Human-Centric to Agentic Code Review
- https://arxiv.org/abs/2505.16339 — Rethinking Code Review Workflows with LLM Assistance
- https://arxiv.org/abs/2608.27442 — MCR-Bench
- https://arxiv.org/abs/2412.18291 — DeepCRCEval
- https://arxiv.org/abs/2604.23667 — Automated Classification of Human Code Review Comments
- https://arxiv.org/abs/2510.24367 — LLM-as-a-Judge for SE
- https://aclanthology.org/2025.naacl-long.457 — CRScore
