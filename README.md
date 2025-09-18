# Job Company Names Extractor (LinkedIn + Indeed) — Windows, No API Keys

Extract **only company names** from **LinkedIn** and **Indeed** job/search pages you list in `input.txt`.  
Works with live URLs and **saved HTML files** (useful for LinkedIn login walls). Outputs a single-column CSV.

## Features

- ✅ Handles **any mix / any count** of LinkedIn & Indeed URLs
- ✅ Matches LinkedIn’s visible **“N results”** list on search pages
- ✅ De-dupes by default; can keep duplicates
- ✅ Optional per-URL report for QA
- ✅ Windows-friendly: paths, retries, polite delay

---

## 1) Install

```bat
py -3 -m pip install -r requirements.txt
```
