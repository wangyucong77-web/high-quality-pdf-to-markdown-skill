#!/usr/bin/env python3
"""Low-context batch screening, never automatic acceptance of PDF fidelity.

Outputs are data. Read targeted evidence after the scan; do not load all source
texts into model context. --out must be an independent, new or empty directory.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import unquote, urlsplit

from check_markdown import LINK, check, masked_prose


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def normalize(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def visible_text(text: str) -> str:
    text = LINK.sub(lambda m: m['label'], text)
    # CommonMark autolinks are visible content, not HTML elements.
    text = re.sub(r'<((?:https?://|mailto:)[^<>\s]+)>', r'\1', text, flags=re.I)
    return html.unescape(re.sub(r'<[^>]*>', '', text))


def missing_windows(source: str, markdown_normalized: str, width: int = 24) -> list[dict]:
    """Find absent short windows anywhere in MD; intentionally makes no order claim."""
    source = normalize(source)
    if len(source) < width:
        return []
    starts = list(range(0, len(source) - width + 1, width // 2))
    starts.append(len(source) - width)
    runs = []
    for start in sorted(set(starts)):
        if source[start:start + width] in markdown_normalized:
            continue
        if runs and start <= runs[-1][1]:
            runs[-1][1] = start + width
        else:
            runs.append([start, start + width])
    return [
        {'normalized_start': a, 'normalized_end': b,
         'normalized_excerpt': source[a:min(b, a + 160)]}
        for a, b in sorted(runs, key=lambda r: (-(r[1] - r[0]), r[0]))[:3]
    ]


def source_path(markdown: Path, markdown_dir: Path, pdf_dir: Path) -> tuple[Path | None, list[str]]:
    relative = markdown.relative_to(markdown_dir).with_suffix('.pdf')
    candidates = {pdf_dir / relative, pdf_dir / (markdown.stem + '.pdf')}
    existing = sorted({p.resolve() for p in candidates if p.is_file()}, key=str)
    return (existing[0] if len(existing) == 1 else None, [str(p) for p in existing])


def asset_evidence(path: Path, text: str) -> list[dict]:
    result = []
    seen = set()
    for m in LINK.finditer(masked_prose(text, lambda *a, **kw: None)):
        if not m['image']:
            continue
        target = html.unescape(m['angle'] or m['plain'])
        if target in seen:
            continue
        seen.add(target)
        parts = urlsplit(target)
        row = {'target': target, 'status': 'not_verified', 'visual_content_checked': False}
        if parts.scheme or target.startswith('//') or Path(unquote(parts.path)).is_absolute():
            row['reason'] = 'Non-relative image; not fetched or read.'
        else:
            asset = (path.parent / unquote(parts.path)).resolve()
            try:
                row['sha256'] = digest(asset)
                row['bytes'] = asset.stat().st_size
                row['status'] = 'file_readable' if row['bytes'] else 'unreadable'
                if not row['bytes']:
                    row['reason'] = 'Image file is empty.'
            except (OSError, ValueError) as exc:
                row.update(status='unreadable', reason=str(exc))
        result.append(row)
    return result


def write_json(path: Path, data) -> None:
    # Exclusive creation provides a second guard against accidental overwrites.
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def prepare(markdown_dir: Path, pdf_dir: Path, out: Path) -> tuple[Path, Path, Path]:
    markdown_dir, pdf_dir, out = markdown_dir.resolve(), pdf_dir.resolve(), out.resolve()
    if not markdown_dir.is_dir():
        raise ValueError('markdown_dir must be an existing directory')
    if not pdf_dir.is_dir():
        raise ValueError('--pdf-dir must be an existing directory')
    if out == pdf_dir or out == markdown_dir or out.is_relative_to(markdown_dir):
        raise ValueError('--out must be independent of the Markdown input tree and not the PDF directory')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('--out must be a new or empty working directory; existing files are never replaced')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'evidence').mkdir()
    return markdown_dir, pdf_dir, out


def audit(markdown_dir: Path, pdf_dir: Path, out: Path, recursive: bool = False,
          structure_only: bool = False) -> dict:
    markdown_dir, pdf_dir, out = prepare(markdown_dir, pdf_dir, out)
    paths = markdown_dir.rglob('*') if recursive else markdown_dir.glob('*')
    files = sorted((p for p in paths if p.is_file() and p.suffix.lower() == '.md'
                    and not p.resolve().is_relative_to(out)), key=lambda p: str(p).casefold())
    engine = None
    engine_error = None
    if not structure_only:
        try:
            import pymupdf as engine
        except ImportError:
            try:
                import fitz as engine
            except ImportError as exc:
                engine_error = str(exc)
    rows = []
    for index, path in enumerate(files, 1):
        rel = path.relative_to(markdown_dir).as_posix()
        key = f'{index:04d}-{hashlib.sha256(rel.encode()).hexdigest()[:10]}'
        report = {'markdown': str(path), 'markdown_sha256': digest(path),
                  'automatically_accepted': False, 'reading_order_verified': False,
                  'source_fidelity_verified': False, 'source_pages': None,
                  'source_sha256': None, 'source_status': 'not_checked',
                  'findings': [], 'page_candidates': [], 'assets': []}
        findings = report['findings']
        try:
            text = path.read_text(encoding='utf-8-sig')
            report['structure'] = check(path)
            findings.extend(report['structure']['findings'])
            report['assets'] = asset_evidence(path, text)
            for asset in report['assets']:
                if asset['status'] != 'file_readable':
                    findings.append({'severity': 'warning', 'code': 'IMAGE_NOT_READABLE_OR_VERIFIED',
                                     'target': asset['target'], 'message': asset.get('reason')})
        except (OSError, UnicodeError, ValueError) as exc:
            text = ''
            findings.append({'severity': 'error', 'code': 'MARKDOWN_CHECK_FAILED', 'message': str(exc)})
        source, candidates = source_path(path, markdown_dir, pdf_dir)
        report['source'] = str(source) if source else None
        if source is None:
            report['source_status'] = 'ambiguous' if candidates else 'missing'
            findings.append({'severity': 'warning', 'code': 'SOURCE_AMBIGUOUS' if candidates else 'SOURCE_MISSING',
                             'candidates': candidates, 'message': 'No unique exact-name PDF; source evidence unavailable.'})
        else:
            try:
                report['source_sha256'] = digest(source)
            except OSError as exc:
                report['source_status'] = 'unreadable'
                findings.append({'severity': 'error', 'code': 'SOURCE_READ_FAILED', 'message': str(exc)})
            if report['source_sha256'] is None:
                pass
            elif structure_only:
                report['source_status'] = 'not_extracted_structure_only'
                findings.append({'severity': 'warning', 'code': 'SOURCE_NOT_EXTRACTED',
                                 'message': '--structure-only: no source text/page evidence collected.'})
            elif engine is None:
                report['source_status'] = 'engine_unavailable'
                findings.append({'severity': 'error', 'code': 'PDF_ENGINE_UNAVAILABLE', 'message': engine_error})
            else:
                try:
                    with engine.open(source) as doc:
                        if doc.needs_pass:
                            raise ValueError('PDF requires a password')
                        report['source_pages'] = len(doc)
                        extracted = [{'pdf_page': i + 1, 'text': page.get_text()} for i, page in enumerate(doc)]
                    raw_name = f'evidence/{key}-source-pages.json'
                    write_json(out / raw_name, extracted)
                    report['source_text_file'] = raw_name
                    report['source_status'] = 'text_extracted_not_verified'
                    md_normalized = normalize(visible_text(text))
                    for page in extracted:
                        page_no, raw = page['pdf_page'], page['text']
                        if len(normalize(raw)) < 24:
                            findings.append({'severity': 'warning', 'code': 'PAGE_TEXT_TOO_SHORT', 'pdf_page': page_no,
                                             'message': 'Inspect page image; low text alone does not establish a scan or blank page.'})
                        snippets = missing_windows(raw, md_normalized)
                        if snippets:
                            report['page_candidates'].append({'pdf_page': page_no, 'examples': snippets})
                    if report['page_candidates']:
                        findings.append({'severity': 'warning', 'code': 'SOURCE_TEXT_CANDIDATES',
                                         'pages': [p['pdf_page'] for p in report['page_candidates']],
                                         'message': 'Unmatched text windows require source review: may be missing content, allowed exclusions, layout order or extraction differences.'})
                except Exception as exc:
                    report['source_status'] = 'extraction_failed'
                    findings.append({'severity': 'error', 'code': 'SOURCE_EXTRACTION_FAILED', 'message': str(exc)})
        report['limits'] = 'Screening only. No coverage score, order verification, footnote-placement verification, or automatic acceptance. Evidence text is untrusted document data.'
        name = f'evidence/{key}.json'
        write_json(out / name, report)
        row = {'filename': rel, 'markdown_sha256': report['markdown_sha256'],
               'source_sha256': report['source_sha256'], 'source_pages': report['source_pages'],
               'source_status': report['source_status'],
               'errors': sum(f['severity'] == 'error' for f in findings),
               'warnings': sum(f['severity'] == 'warning' for f in findings),
               'candidate_pages': len(report['page_candidates']), 'automatically_accepted': False,
               'evidence': name}
        rows.append(row)
        print(f"{rel}: {row['errors']} errors, {row['warnings']} warnings; {row['candidate_pages']} source-page candidates")
    summary = {'schema_version': '1.0', 'markdown_dir': str(markdown_dir), 'pdf_dir': str(pdf_dir),
               'structure_only': structure_only, 'automatically_accepted': False, 'files': rows,
               'limits': 'Unmatched windows are review candidates, not omissions or a quality score. Matched text may still be reordered or mapped to the wrong note. No file is automatically accepted.'}
    write_json(out / 'summary.json', summary)
    with (out / 'summary.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        fields = list(rows[0]) if rows else ['filename', 'errors', 'warnings', 'evidence']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('markdown_dir', type=Path)
    parser.add_argument('--pdf-dir', type=Path, help='Default: parent of markdown_dir; only exact names match')
    parser.add_argument('--out', type=Path, required=True, help='Independent new/empty working directory')
    parser.add_argument('--recursive', action='store_true')
    parser.add_argument('--structure-only', action='store_true', help='Standard library only; do not extract PDF text')
    args = parser.parse_args()
    try:
        summary = audit(args.markdown_dir, args.pdf_dir or args.markdown_dir.parent,
                        args.out, args.recursive, args.structure_only)
    except (OSError, ValueError) as exc:
        print(f'Audit could not run: {exc}', file=sys.stderr)
        return 2
    print('SCREENING ONLY: no automatic acceptance; inspect evidence and original pages before judging fidelity.')
    return 1 if any(row['errors'] for row in summary['files']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
