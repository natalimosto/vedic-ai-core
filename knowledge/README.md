# Knowledge Base

This folder stores source documents and extracted chunks used for AI interpretation.

Structure:
- `sources/`  Original files (PDFs, notes)
- `chunks/`   Extracted JSONL chunks (one record per line)
- `manifest.json`  Optional metadata about sources

Ingestion:
Run:
```
python tools/ingest_pdf.py --input knowledge/sources --output knowledge/chunks
```

The script will create `.jsonl` files with fields:
- `source`
- `page`
- `chunk_index`
- `text`
