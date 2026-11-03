#!/usr/bin/env python3
"""Read-only PDF inspection; coordinates and object order are not reading order."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path


def page_selection(value: str, count: int) -> list[int]:
    result: set[int] = set()
    for part in value.split(','):
        part = part.strip()
        if not part:
            continue
        bits = part.split('-')
        if len(bits) == 1:
            start = end = int(bits[0])
        elif len(bits) == 2:
            start, end = map(int, bits)
        else:
            raise ValueError(f'Invalid page range: {part}')
        if not 1 <= start <= end <= count:
            raise ValueError(f'Pages must be between 1 and {count}: {part}')
        result.update(range(start - 1, end))
    if not result:
        raise ValueError('No pages selected')
    return sorted(result)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def inspect(source: Path, out: Path, render: str, pages: str | None, dpi: int) -> dict:
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError('PyMuPDF is required. Use an available runtime or install pymupdf.') from exc

    source = source.resolve(strict=True)
    out = out.resolve()
    if out == source or source in out.parents:
        raise ValueError('Output must be a working directory, not the source file')
    with fitz.open(source) as doc:
        if doc.needs_pass:
            raise RuntimeError('Encrypted PDF requires an authorized decrypted copy')
        if len(doc) == 0:
            raise RuntimeError('PDF has no pages')
        selected = page_selection(pages, len(doc)) if pages else list(range(len(doc)))
        if render == 'selected' and not pages:
            raise ValueError('--render selected requires --pages, e.g. 1,3-5')
        out.mkdir(parents=True, exist_ok=True)
        report = {
            'schema_version': '1.0',
            'source': {'filename': source.name, 'sha256': digest(source), 'pdf_pages': len(doc)},
            'notice': 'Raw object order and coordinates only. Not a verified reading edition.',
            'pages': [],
            'renders': [],
        }
        text_output = []
        for index, page in enumerate(doc):
            text = page.get_text('text')
            raw = page.get_text('dict')
            blocks = []
            for block_index, block in enumerate(raw.get('blocks', [])):
                if 'lines' not in block:
                    continue
                rows = []
                for line in block['lines']:
                    rows.append({
                        'bbox': list(line['bbox']),
                        'direction': list(line.get('dir', [1, 0])),
                        'text': ''.join(span['text'] for span in line['spans']),
                        'spans': [
                            {key: span[key] for key in ['text', 'font', 'size', 'bbox', 'origin', 'flags'] if key in span}
                            for span in line['spans']
                        ],
                    })
                blocks.append({'object_index': block_index, 'bbox': list(block['bbox']), 'lines': rows})
            report['pages'].append({
                'pdf_page': index + 1,
                'width': page.rect.width,
                'height': page.rect.height,
                'rotation': page.rotation,
                'non_whitespace_characters': sum(not c.isspace() for c in text),
                'embedded_image_objects': len(page.get_images(full=True)),
                'text_blocks': blocks,
            })
            text_output.append(f'=== PDF PAGE {index + 1} (RAW OBJECT ORDER) ===\n{text}')

        if render == 'selected':
            for index in selected:
                target = out / f'page-{index + 1:04d}.png'
                doc[index].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False).save(target)
                report['renders'].append({'path': target.name, 'pdf_pages': [index + 1]})
        elif render == 'contact':
            # A PDF sheet avoids making Pillow another dependency.
            batch_size, columns, cell_w, cell_h = 12, 3, 240, 340
            for start in range(0, len(selected), batch_size):
                batch = selected[start:start + batch_size]
                with fitz.open() as sheet:
                    sheet_page = sheet.new_page(width=columns * cell_w, height=math.ceil(len(batch) / columns) * cell_h)
                    for slot, index in enumerate(batch):
                        x = slot % columns * cell_w
                        y = slot // columns * cell_h
                        sheet_page.insert_text((x + 10, y + 16), f'PDF page {index + 1}', fontsize=10)
                        rect = fitz.Rect(x + 8, y + 25, x + cell_w - 8, y + cell_h - 8)
                        # Empty pages have no content stream; a blank slot is correct.
                        if doc[index].get_contents():
                            sheet_page.show_pdf_page(rect, doc, index)
                    target = out / f'contact-{start // batch_size + 1:03d}.png'
                    sheet_page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False).save(target)
                    report['renders'].append({'path': target.name, 'pdf_pages': [i + 1 for i in batch]})
        (out / 'inspection.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / 'page-text.txt').write_text('\n\n'.join(text_output), encoding='utf-8')
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--render', choices=['none', 'contact', 'selected'], default='none')
    parser.add_argument('--pages', help='1-based page list for rendering only, e.g. 1,3-5')
    parser.add_argument('--dpi', type=int, default=144, help='Selected-page render DPI (72-400)')
    args = parser.parse_args()
    if not 72 <= args.dpi <= 400:
        parser.error('--dpi must be between 72 and 400')
    try:
        report = inspect(args.pdf, args.out, args.render, args.pages, args.dpi)
    except Exception as exc:
        print(f'Inspection failed: {exc}', file=sys.stderr)
        return 2
    print(f'Inspected {report["source"]["pdf_pages"]} pages; {len(report["renders"])} render files.')
    print(f'Working output: {args.out.resolve()}')
    print('Raw text is not a verified reading order. Inspect rendered pages and source coordinates.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
