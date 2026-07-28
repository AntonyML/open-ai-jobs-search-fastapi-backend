import sys, json, os
sys.path.insert(0, r"C:\DEV\open-ai-jobs-search\open-ai-jobs-search-fastapi-backend")
from app.services.pdf_compiler_typst import compile_cv

examples = r"C:\DEV\open-ai-jobs-search\open-ai-jobs-search-fastapi-backend\app\examples"
out_dir = os.path.join(examples, "_output_typst")
os.makedirs(out_dir, exist_ok=True)

import glob
for f in sorted(glob.glob(os.path.join(examples, "cv_*.json"))):
    name = os.path.basename(f).replace(".json", "")
    with open(f, encoding="utf-8") as fh:
        cv_data = json.load(fh)

    pdf_path = os.path.join(out_dir, name + ".pdf")
    try:
        compile_cv(cv_data, output=pdf_path)
        sz = os.path.getsize(pdf_path)
        print(f"{name:35s} OK  {sz:>8,} bytes")
    except Exception as e:
        print(f"{name:35s} FAIL  {type(e).__name__}: {e}")

print(f"\nAll outputs in: {out_dir}")
