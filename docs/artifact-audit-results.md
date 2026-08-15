# Artifact storage audit

- Storage roots: `E:\Dev\Personal\open-ai-jobs-search\open-ai-jobs-search-fastapi-backend\generated_cvs` (CV generator), `E:\Dev\Personal\open-ai-jobs-search\open-ai-jobs-search-fastapi-backend\generated` (apply pipeline)
- Database reachable: True

## Summary

| Category | Count | Bytes | Recommended action |
|---|---:|---:|---|
| active | 4 | 117.9 KB | keep |
| referenced | 0 | 0 B | keep |
| deleted | 4 | 125.0 KB | remove file (retention 0 — file is derived; row kept for audit) |
| orphan | 1 | 27.5 KB | remove file |
| unexpected | 0 | 0 B | review before removing |

- Empty directories: **1**
- Active rows with a missing file (broken download): **0**
- Rows with `pdf_path = NULL` (compile failed): **0**

## CV generator files

| Category | File | Bytes |
|---|---:|---:|
| active | `d2549742-5420-4f2b-b2ef-74910a7b5a39/13fc5f18-946b-436e-9d39-c4c93ec0c421.pdf` | 30551 |
| deleted | `d2549742-5420-4f2b-b2ef-74910a7b5a39/35426b44-720c-4b4f-93ec-b42ab665860c.pdf` | 29409 |
| active | `d2549742-5420-4f2b-b2ef-74910a7b5a39/450d38f0-9946-4e1c-9bef-19d6f43dc1f4.pdf` | 30901 |
| deleted | `d2549742-5420-4f2b-b2ef-74910a7b5a39/74df2cc2-7c54-4298-b9ca-89ffa4452e2f.pdf` | 28620 |
| active | `d2549742-5420-4f2b-b2ef-74910a7b5a39/90c6ea79-0cbb-4eb2-9637-937709e09120.pdf` | 29422 |
| orphan | `d2549742-5420-4f2b-b2ef-74910a7b5a39/9c3dcd66-c77a-4aa3-9572-aff53991ba5d.pdf` | 28148 |
| deleted | `d2549742-5420-4f2b-b2ef-74910a7b5a39/c0a2f4f2-6a95-4b84-9327-8501356a1d1a.pdf` | 34917 |
| active | `d2549742-5420-4f2b-b2ef-74910a7b5a39/ddc091ff-de15-42fd-9bc2-447940e4b4f4.pdf` | 29808 |
| deleted | `d2549742-5420-4f2b-b2ef-74910a7b5a39/e60dd576-5999-4986-aa03-d6cd1cc66f12.pdf` | 35055 |

## Empty directories

- `E:\Dev\Personal\open-ai-jobs-search\open-ai-jobs-search-fastapi-backend\generated_cvs\test-user-id`
