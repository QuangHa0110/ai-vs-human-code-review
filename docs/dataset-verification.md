# Xác minh nguồn dữ liệu trước khi viết adapter

Ngày 2026-11-24. Khảo sát chỉ đọc bởi `dataset_recon`; chưa chạy AI.

## D1 — AACR-Bench

Revision GitHub: `07ba1b35102110404f6a931bf05dea33e6d89389`.

- [Positive](https://github.com/alibaba/aacr-bench/blob/07ba1b35102110404f6a931bf05dea33e6d89389/dataset/v1/positive_samples.json), [negative](https://github.com/alibaba/aacr-bench/blob/07ba1b35102110404f6a931bf05dea33e6d89389/dataset/v1/negative_samples.json).
- Mỗi file là mảng PR; chọn `project_main_language == "Java"`. Positive: 30 PR, 213 nhận xét (61 người, 152 AI). Negative: 18 PR, 59 nhận xét (16 người, 43 AI), đều nằm trong 30 PR positive.
- PR có `githubPrUrl`, `source_commit`, `target_commit`, `comments`. Nhận xét có `is_ai_comment`, `note`, `path`, `side`, `from_line`, `to_line`, `category`, `context`.
- [Converter chính thức](https://github.com/alibaba/aacr-bench/blob/07ba1b35102110404f6a931bf05dea33e6d89389/evaluation/converters/aacr_bench.py#L185-L222) ánh xạ source→base, target→review head. Giữ SHA của benchmark, không lấy head hiện tại trên GitHub. File không chứa diff.
- [Định nghĩa nhãn](https://huggingface.co/datasets/Alibaba-Aone/aacr-bench#data-sample): 0 = incorrect, 1 = correct, độc lập với nguồn người/AI. Số tổng trên card khác GitHub; dùng revision GitHub khi đối chiếu số lượng.
- Có một nhận xét positive Java phía LEFT. Cần báo cáo số thô và số sau lọc riêng, không khẳng định 213 là số sau lọc.

## D3 — SWE-PRBench

Revision HuggingFace: `b87f5797aef3ed2c3153bb1304ea4d801d36ba6e`.

- [Tập đánh giá](https://huggingface.co/datasets/foundry-ai/swe-prbench/resolve/b87f5797aef3ed2c3153bb1304ea4d801d36ba6e/dataset/evals/eval_100.json) thực tế là mảng 100 PR đầy đủ, không phải object chứa danh sách ID.
- Tập đầy đủ `dataset/prs.jsonl`: 350 PR, 15 Java. Tập đánh giá: **3 Java, 67 Python**, còn lại JS/Go/TS.
- Java được đánh giá: `lucene__15223`, `nessie__11676`, `firebase-android-sdk__7617`.
- Row gồm `task_id`, `repo`, `pr_number`, `pr_url`, `language`, `base_commit`, `head_commit`, `diff_patch`, `human_review_comments`. Diff có sẵn.
- Nhãn chuẩn nằm tại `dataset/annotations/{task_id}_human.json`: object có `substantive_comment_ids`, `comments`; comment có `comment_id`, `body`, `file`, `line`, `is_in_diff`, `is_initiating_comment`, `is_reply`.
- [Nessie](https://huggingface.co/datasets/foundry-ai/swe-prbench/resolve/b87f5797aef3ed2c3153bb1304ea4d801d36ba6e/dataset/annotations/nessie__11676_human.json) có nhận xét ngoài diff và nhận xét line=null; adapter cần quy tắc rõ cho chúng.
- Không coi raw `human_review_comments` (có reply, schema path/diffHunk/replyTo) là toàn bộ nhãn substantive đã xác minh.
