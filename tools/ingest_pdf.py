import argparse
import json
import os
from pathlib import Path

from pypdf import PdfReader


def chunk_text(text: str, chunk_size: int, overlap: int):
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if overlap < 0:
        raise ValueError("overlap must be >= 0")
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
        if start < 0:
            start = 0
        if start >= text_len:
            break
    return chunks


def ingest_pdf(pdf_path: Path, output_dir: Path, chunk_size: int, overlap: int):
    reader = PdfReader(str(pdf_path))
    out_path = output_dir / f"{pdf_path.stem}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for page_index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if not text.strip():
                continue
            for idx, chunk in enumerate(chunk_text(text, chunk_size, overlap)):
                record = {
                    "source": pdf_path.name,
                    "page": page_index,
                    "chunk_index": idx,
                    "text": chunk,
                }
                f.write(json.dumps(record, ensure_ascii=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Ingest PDFs into JSONL chunks.")
    parser.add_argument("--input", required=True, help="Directory with PDF files")
    parser.add_argument("--output", required=True, help="Directory for JSONL chunks")
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--overlap", type=int, default=150)
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(input_dir.glob("*.pdf"))
    if not pdf_files:
        raise SystemExit("No PDF files found in input directory.")

    for pdf_path in pdf_files:
        ingest_pdf(pdf_path, output_dir, args.chunk_size, args.overlap)


if __name__ == "__main__":
    main()
