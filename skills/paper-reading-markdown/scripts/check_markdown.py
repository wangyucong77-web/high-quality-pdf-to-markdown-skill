#!/usr/bin/env python3
"""Check the reading-edition contract, not source fidelity. Standard library only."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import html
import json
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import unquote, urlsplit


ANCHOR = re.compile(r'<[A-Za-z][^>]*?\b(?:id|name)\s*=\s*([\x27\"])([^\x27\"]+)\1[^>]*>', re.I)
LINK = re.compile(r'(?P<image>!)?\[(?P<label>[^\n]*?)\]\(\s*(?:<(?P<angle>[^>]+)>|(?P<plain>[^\s)]+))(?:\s+[\x27\"][^\n]*?[\x27\"])?\s*\)')
FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')


def comparable(value: str) -> str:
    """Ignore layout punctuation, not the order of the substantive characters."""
    value = re.sub(r'<[^>]+>', '', value)
    value = LINK.sub(lambda m: '' if (m['angle'] or m['plain']).startswith('#note-') else m['label'], value)
    return ''.join(c.casefold() for c in unicodedata.normalize('NFKC', value) if c.isalnum())


def note_field(block: str, name: str) -> str:
    match = re.search(r'^[ \t]*(?:\*\*)?' + re.escape(name) + r'[ \t]*[：:][ \t]*(?:\*\*)?[ \t]*(.*?)\s*$', block, re.M)
    # A closing bold delimiter is not a field value, and a blank value must not
    # borrow the next paragraph as metadata.
    if match and match[1].strip() in {'', '**'}:
        return ''
    return match[1].strip() if match else ''


def masked_prose(text: str, add) -> str:
    """Mask code, preserving offsets so diagnostics refer to the original file."""
    result = []
    fence = None
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        match = FENCE.match(line.rstrip('\r\n'))
        if fence:
            result.append(re.sub(r'[^\r\n]', ' ', line))
            if match and match[1][0] == fence[0] and len(match[1]) >= fence[1] and not match[2].strip():
                fence = None
            continue
        if match:
            fence = (match[1][0], len(match[1]), number)
            result.append(re.sub(r'[^\r\n]', ' ', line))
        else:
            result.append(line)
    if fence:
        add('error', 'UNCLOSED_FENCE', 'Code fence is not closed.', line=fence[2])
    prose = ''.join(result)
    return re.sub(r'(`+)([^\n]*?)\1', lambda m: ' ' * len(m[0]), prose)


def check(path: Path, manifest_path: Path | None = None) -> dict:
    path = path.resolve(strict=True)
    text = path.read_text(encoding='utf-8-sig')
    findings = []

    def add(severity, code, message, **details):
        findings.append({'severity': severity, 'code': code, 'message': message, **details})

    def line_at(offset):
        return text.count('\n', 0, offset) + 1

    if not text.strip():
        add('error', 'EMPTY_DOCUMENT', 'Markdown has no readable content.')
    prose = masked_prose(text, add)
    anchor_matches = list(ANCHOR.finditer(prose))
    counts = Counter(m[2] for m in anchor_matches)
    positions = {m[2]: m for m in anchor_matches}
    for key, count in counts.items():
        if count > 1:
            add('error', 'DUPLICATE_ANCHOR', f'Anchor {key!r} is defined {count} times.')
    headings = []
    previous_level = 0
    for match in re.finditer(r'^ {0,3}(#{1,6})\s+(.+?)\s*#*$', prose, re.M):
        level = len(match[1])
        headings.append({'level': level, 'text': match[2], 'line': line_at(match.start())})
        if len(match[2]) > 180:
            add('warning', 'LONG_HEADING_REVIEW', 'Heading may have absorbed body text; compare the source heading boundary.', line=line_at(match.start()))
        if previous_level and level > previous_level + 1:
            add('warning', 'HEADING_LEVEL_JUMP', f'Heading jumps from level {previous_level} to {level}.', line=line_at(match.start()))
        previous_level = level
    title_count = sum(h['level'] == 1 for h in headings)
    if title_count != 1:
        add('error', 'DOCUMENT_TITLE', f'Expected one level-1 title, found {title_count}.')

    for heading in headings:
        if re.search(r'未能定位引注位置的注释|未定位(?:的)?(?:脚注|注释)|unlocated\s+(?:footnotes|notes)', heading['text'], re.I):
            add('error', 'UNRESOLVED_NOTE_LOCATION', 'Document explicitly contains notes with unresolved reference locations.', line=heading['line'])
    # Match editorial status statements, not the word "candidate" in research prose.
    candidate_lines = []
    pending_lines = []
    for number, line in enumerate(prose.splitlines(), 1):
        if re.search(r'^\s*(?:>\s*|-\s*)?(?:由\s*PDF\s*自动转换的候选阅读稿|本文件为\s*PDF\s*自动转换的候选阅读稿)', line, re.I):
            candidate_lines.append(number)
        if re.search(r'^\s*(?:>\s*|-\s*)?待核验\s*[：:]', line):
            pending_lines.append(number)
    if candidate_lines:
        add('warning', 'CANDIDATE_STATUS', 'Document declares itself an unverified candidate, so acceptance is blocked.', lines=candidate_lines)
    if pending_lines:
        add('warning', 'PENDING_VERIFICATION', 'Document explicitly lists unresolved verification items.', lines=pending_lines)

    links = []
    images = []
    all_local_paths = set()
    for match in LINK.finditer(prose):
        target = html.unescape(match['angle'] or match['plain'])
        record = {'target': target, 'label': match['label'], 'line': line_at(match.start()), 'start': match.start()}
        if match['image']:
            images.append(record)
            if not match['label'].strip():
                add('warning', 'IMAGE_ALT_EMPTY', 'Image has no descriptive alternative text.', line=record['line'])
            windows_absolute = bool(re.match(r'^[A-Za-z]:[\\/]', target))
            parts = urlsplit(target)
            if parts.scheme in {'http', 'https', 'data'}:
                add('warning', 'NONLOCAL_IMAGE', 'Image is not a portable local asset; not downloaded or verified.', target=target, line=record['line'])
                continue
            decoded = unquote(parts.path) if not windows_absolute else target
            asset = Path(decoded)
            if asset.is_absolute() or windows_absolute or parts.scheme == 'file':
                add('error', 'ABSOLUTE_IMAGE_PATH', 'Use a relative image path for portability.', target=target, line=record['line'])
                continue
            if parts.scheme:
                add('error', 'UNSUPPORTED_IMAGE_URI', 'Unsupported image URI.', target=target, line=record['line'])
                continue
            resolved = (path.parent / asset).resolve()
            all_local_paths.add(str(resolved))
            if not resolved.is_file():
                add('error', 'MISSING_IMAGE', 'Image file does not exist.', target=target, line=record['line'])
        else:
            links.append(record)
            if target.startswith('#'):
                key = unquote(target[1:])
                if key not in positions:
                    add('error', 'MISSING_LINK_TARGET', 'Internal link has no explicit anchor target.', target=target, line=record['line'])

    note_matches = sorted((m for m in anchor_matches if m[2].startswith('note-')), key=lambda m: m.start())
    note_ids = {m[2][5:] for m in note_matches}
    reference_ids = {m[2] for m in anchor_matches if m[2].startswith('ref-')}
    pairs = defaultdict(set)
    mapped_references = set()
    note_sections = []
    for link in links:
        if not link['target'].startswith('#note-'):
            continue
        note_target = unquote(link['target'][1:])
        candidates = [m for m in anchor_matches if m[2].startswith('ref-') and m.end() <= link['start']]
        preceding = max(candidates, key=lambda m: m.end(), default=None)
        between = prose[preceding.end():link['start']] if preceding else ''
        # ANCHOR matches the opening element; an empty anchor's closing tag is
        # allowed before its Markdown link, and is not intervening prose.
        if preceding is None or re.sub(r'</a\s*>', '', between, flags=re.I).strip():
            add('error', 'UNLOCATED_NOTE_REFERENCE', 'A forward note link needs its own immediately preceding ref-* anchor.', line=link['line'])
            continue
        if preceding[2] in mapped_references:
            add('error', 'SHARED_REFERENCE_ANCHOR', 'Multiple forward links share a return location.', anchor=preceding[2])
        mapped_references.add(preceding[2])
        pairs[note_target].add(preceding[2])
    for ref in sorted(reference_ids - mapped_references):
        add('error', 'ORPHAN_REFERENCE', 'ref-* anchor is not attached to a forward note link.', anchor=ref)
    for index, match in enumerate(note_matches):
        note = match[2]
        end = note_matches[index + 1].start() if index + 1 < len(note_matches) else len(prose)
        block = prose[match.end():end]
        returns = {unquote(link['target'][1:]) for link in links if match.end() <= link['start'] < end and link['target'].startswith('#ref-')}
        expected_returns = pairs.get(note, set())
        if not expected_returns:
            add('error', 'ORPHAN_NOTE', 'Note has no located forward reference.', anchor=note)
        for ref in sorted(expected_returns - returns):
            add('error', 'MISSING_NOTE_RETURN', 'Note is missing a return link to its own reference.', note=note, reference=ref)
        for ref in sorted(returns - expected_returns):
            add('error', 'WRONG_NOTE_RETURN', 'Note returns to a reference belonging to another note or no note.', note=note, reference=ref)
        if not re.search(r'(?:原文|PDF|source page)[^\n]{0,45}(?:页|page|\d)', block, re.I):
            add('error', 'NOTE_SOURCE_PAGE', 'No source-page label recognized in note.', anchor=note)
        for field in ['对应章节', '引注文字']:
            if not note_field(block, field):
                add('error', 'NOTE_CONTEXT', f'Note lacks {field}.', anchor=note)
        section = note_field(block, '对应章节')
        cue = note_field(block, '引注文字')
        note_sections.append((note, section))
        if section and (len(section) > 120 or re.fullmatch(r'关键词|摘要|Keywords|Abstract', section, re.I)):
            add('warning', 'NOTE_SECTION_SUSPICIOUS', 'Note section label looks like metadata or includes a long body passage; verify against source headings.', anchor=note, section=section[:160])
        if cue and expected_returns:
            # Cue must be near at least one return position; exact source placement
            # still needs PDF comparison even when this test succeeds.
            cue_parts = [comparable(part) for part in re.split(r'(?:…+|\.{3,}|\[\.\.\.\]|〔省略〕)', cue)]
            cue_parts = [part for part in cue_parts if len(part) >= 6]
            neighborhoods = []
            for ref in expected_returns:
                if ref not in positions:
                    continue
                reference_start = positions[ref].start()
                neighborhood_end = reference_start + 350
                # Do not "verify" a cue by finding the cue itself in the note.
                following_note = next((item.start() for item in note_matches if item.start() > reference_start), len(prose))
                neighborhood_end = min(neighborhood_end, following_note)
                neighborhoods.append(comparable(prose[max(0, reference_start - 850):neighborhood_end]))
            if cue_parts and not any(all(part in neighborhood for part in cue_parts) for neighborhood in neighborhoods):
                add('warning', 'NOTE_CUE_NOT_NEAR_REFERENCE', 'Quoted cue is not found near its return anchor; inspect source and exact placement.', anchor=note)
        # Restrict length/embedded-label heuristics to the note text, excluding
        # the return links and subsequent sections.
        content_start = re.search(r'^\s*(?:\*\*)?引注文字\s*[：:].*$', block, re.M)
        content = block[content_start.end():] if content_start else block
        first_return = next((link['start'] - match.end() for link in links if match.end() <= link['start'] < end and link['target'].startswith('#ref-')), len(block))
        if content_start:
            content = block[content_start.end():first_return]
        else:
            content = block[:first_return]
        note_length = len(comparable(content))
        if note_length > 1800:
            add('warning', 'LONG_NOTE_REVIEW', 'Unusually long note: verify it is not merged notes or body text. Legitimate long notes must be retained.', anchor=note, normalized_characters=note_length)
        embedded_labels = re.findall(r'(?:^|\s)(?:〔+\s*(\d{1,3})\s*〕+|\[\s*(\d{1,3})\s*\]|(\d{1,3})[.．、]\s+)', content, re.M)
        labels = {next(value for value in group if value) for group in embedded_labels}
        if len(labels) >= 3:
            add('warning', 'POSSIBLY_MERGED_NOTES', 'Several numbered labels occur inside one note; determine whether they are legitimate cited items or merged source notes.', anchor=note, labels=sorted(labels))
    section_counts = Counter(section for _, section in note_sections if section)
    if len(note_matches) >= 5 and section_counts:
        most_common, occurrences = section_counts.most_common(1)[0]
        if occurrences / len(note_matches) >= .8:
            add('warning', 'REPEATED_NOTE_SECTION', 'Most notes have the same section label; source comparison must establish whether this is correct.', section=most_common[:160], count=occurrences)
    if not note_matches:
        loose_marks = re.findall(r'〔+\s*\d{1,3}\s*〕+|[①-⑳]', prose)
        if loose_marks:
            add('warning', 'UNSTRUCTURED_NOTE_MARKERS', 'Numbered markers exist but no structured notes were found. Determine from PDF whether these are bibliography citations, footnotes, or list labels; do not automatically convert them.', count=len(loose_marks))
    if re.search(r'^\[\^[^\]]+\]:', prose, re.M):
        add('warning', 'EXTENSION_FOOTNOTES', 'Standard footnote extension syntax detected; verify target renderer support explicitly.')

    references_section = re.search(r'^##\s+(?:References|参考文献)\s*$', prose, re.M | re.I)
    reference_entries = []
    if references_section:
        rest = prose[references_section.end():]
        next_section = re.search(r'^##\s+', rest, re.M)
        if next_section:
            rest = rest[:next_section.start()]
        reference_entries = re.findall(r'^\s*(?:[-*]\s+)?\[(\d+)\]\s+', rest, re.M)
        if len(reference_entries) != len(set(reference_entries)):
            add('error', 'DUPLICATE_REFERENCE_LABEL', 'Numbered bibliography entries repeat a label.')

    observed = {
        'note_ids': sorted(note_ids),
        'note_count': len(note_ids),
        'reference_locations': len(mapped_references),
        'figure_count': sum(key.startswith('figure-') for key in positions),
        'table_count': sum(key.startswith('table-') for key in positions),
        'listing_count': sum(key.startswith('listing-') for key in positions),
        'reference_count': len(reference_entries) if references_section and reference_entries else None,
        'image_links': len(images),
        'heading_count': len(headings),
    }
    source_review_declared = False
    unresolved_manifest = False
    if manifest_path:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        if not isinstance(manifest, dict):
            raise ValueError('Manifest must be a JSON object')
        if manifest.get('example_only'):
            add('warning', 'EXAMPLE_MANIFEST', 'Manifest is marked as a fictional example; do not treat it as source evidence.')
        expected = manifest.get('expected', {})
        if not isinstance(expected, dict):
            raise ValueError('manifest.expected must be an object')
        if expected.get('note_ids') is not None:
            declared = [str(value) for value in expected['note_ids']]
            if len(declared) != len(set(declared)):
                add('error', 'MANIFEST_DUPLICATE_NOTE_ID', 'Manifest repeats a note ID.')
            if set(declared) != note_ids:
                add('error', 'NOTE_INVENTORY_MISMATCH', 'Observed note IDs differ from source inventory.', expected=sorted(declared), observed=sorted(note_ids))
        for key in ['note_count', 'figure_count', 'table_count', 'listing_count', 'reference_count']:
            value = expected.get(key)
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f'manifest.expected.{key} must be a nonnegative integer or null')
            if observed[key] is None:
                add('warning', 'UNCOUNTED_INVENTORY', f'{key} could not be counted using the output convention.')
            elif observed[key] != value:
                add('error', 'INVENTORY_COUNT_MISMATCH', f'{key} does not match source inventory.', expected=value, observed=observed[key])
        for section in manifest.get('sections', []):
            if section.get('anchor') not in positions:
                add('error', 'MANIFEST_SECTION_MISSING', 'Declared section anchor is absent.', anchor=section.get('anchor'))
        for note in manifest.get('notes', []):
            target = 'note-' + str(note['id'])
            actual = pairs.get(target, set())
            declared = set(note.get('reference_ids', []))
            if declared != actual:
                add('error', 'MANIFEST_NOTE_LOCATION_MISMATCH', 'Return locations differ from recorded source mapping.', note=target, expected=sorted(declared), observed=sorted(actual))
            if note.get('section_anchor') and note['section_anchor'] not in positions:
                add('error', 'NOTE_SECTION_MISSING', 'Note names an absent section anchor.', note=target)
        for asset in manifest.get('assets', []):
            relative = Path(unquote(asset['path']))
            if relative.is_absolute() or re.match(r'^[A-Za-z]:[\\/]', asset['path']):
                add('error', 'MANIFEST_ASSET_ABSOLUTE', 'Manifest assets must use Markdown-relative paths.', target=asset['path'])
                continue
            resolved = (path.parent / relative).resolve()
            if not resolved.is_file():
                add('error', 'MANIFEST_ASSET_MISSING', 'Declared asset is absent.', target=asset['path'])
            elif str(resolved) not in all_local_paths:
                add('warning', 'MANIFEST_ASSET_UNUSED', 'Declared asset is not referenced by a Markdown image.', target=asset['path'])
            if asset.get('anchor') and asset['anchor'] not in positions:
                add('error', 'MANIFEST_ASSET_ANCHOR_MISSING', 'Declared asset anchor is absent.', anchor=asset['anchor'])
        if manifest.get('uncertainties'):
            unresolved_manifest = True
            add('warning', 'UNRESOLVED_SOURCE_ITEMS', 'Manifest still records source uncertainties; review and disclose them.')
        review = manifest.get('review', {})
        if review.get('human_review_complete', False):
            gates = ['source_coverage_checked', 'reading_order_checked', 'note_positions_checked', 'figures_tables_checked', 'markdown_preview_checked']
            incomplete = [gate for gate in gates if review.get(gate) is not True]
            if incomplete:
                add('error', 'REVIEW_STATE_CONFLICT', 'Complete source review is declared while required review gates are incomplete.', incomplete=incomplete)
            else:
                source_review_declared = not manifest.get('example_only', False)
        else:
            add('warning', 'SOURCE_REVIEW_UNCONFIRMED', 'Full source comparison has not been declared complete in the working record.')
    else:
        add('warning', 'NO_SOURCE_INVENTORY', 'No manifest: missing source content and expected counts cannot be assessed.')

    blocking_reasons = sorted({f['code'] for f in findings if f['severity'] == 'error'})
    blocking_reasons.extend(code for condition, code in [(bool(candidate_lines), 'CANDIDATE_STATUS'), (bool(pending_lines), 'PENDING_VERIFICATION'), (unresolved_manifest, 'UNRESOLVED_SOURCE_ITEMS')] if condition)
    acceptance_status = 'blocked' if blocking_reasons else ('review_declared_not_verified' if source_review_declared else 'source_review_required')
    return {
        'schema_version': '1.1',
        'markdown': str(path),
        'automated_checks_passed': not any(f['severity'] == 'error' for f in findings),
        'human_review_complete': None,
        'acceptance': {
            'status': acceptance_status,
            'automatically_accepted': False,
            'blocking_reasons': blocking_reasons,
            'source_review_declared': source_review_declared,
            'explanation': 'A source-review declaration is user-supplied evidence, not independent verification by this script. No automatic acceptance of source fidelity is possible.',
        },
        'scope': 'Structure, explicit targets, local resources, note links, and optional declared inventory only. Source fidelity, order, note placement and visual correctness are NOT established.',
        'observed': observed,
        'findings': findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('markdown', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        if args.report:
            protected = {args.markdown.resolve()}
            if args.manifest:
                protected.add(args.manifest.resolve())
            if args.report.resolve() in protected or args.report.suffix.lower() != '.json':
                raise ValueError('--report must be a separate .json working file')
        report = check(args.markdown, args.manifest)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as exc:
        print(f'Check could not run: {exc}', file=sys.stderr)
        return 2
    errors = sum(f['severity'] == 'error' for f in report['findings'])
    warnings = sum(f['severity'] == 'warning' for f in report['findings'])
    print(f'Automatic structure check: {errors} errors, {warnings} warnings; {report["observed"]["note_count"]} notes.')
    print(f'Acceptance status: {report["acceptance"]["status"]}; automatic source acceptance is never granted.')
    for finding in report['findings']:
        location = f' line {finding["line"]}' if finding.get('line') else ''
        print(f'{finding["severity"].upper()} {finding["code"]}{location}: {finding["message"]}')
    print('This result does not establish source fidelity or complete human review.')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
