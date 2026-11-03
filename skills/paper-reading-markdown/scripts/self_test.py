#!/usr/bin/env python3
"""Behavioral regression checks. Run from any directory; no papers are modified."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from check_markdown import check


BASE = '''# Test paper

<a id="section-1"></a>

## Body

Sentence A.<a id="ref-1"></a>[〔1〕](#note-1)
Sentence B.<a id="ref-2"></a>[〔2〕](#note-2)

<a id="note-1"></a>

## Notes

### 注1〔原文第1页〕

**对应章节：** Body

**引注文字：** Sentence A.

Full source note A.

[Return A](#ref-1)

<a id="note-2"></a>

### 注2〔原文第2页〕

**对应章节：** Body

**引注文字：** Sentence B.

Full source note B.

[Return B](#ref-2)
'''


class ReadingChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='paper-reading-skill-test-')
        self.work = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_check(self, content=BASE, manifest=None):
        target = self.work / 'paper.md'
        target.write_text(content, encoding='utf-8')
        manifest_path = None
        if manifest is not None:
            manifest_path = self.work / 'manifest.json'
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        return check(target, manifest_path)

    def codes(self, report):
        return {f['code'] for f in report['findings'] if f['severity'] == 'error'}

    def test_complete_pairs(self):
        result = self.run_check()
        self.assertTrue(result['automated_checks_passed'])
        self.assertEqual(result['observed']['reference_locations'], 2)
        self.assertIsNone(result['human_review_complete'])
        self.assertEqual(result['acceptance']['status'], 'source_review_required')
        self.assertFalse(result['acceptance']['automatically_accepted'])

    def test_unlocated_note_section_blocks_acceptance(self):
        result = self.run_check(BASE + '\n## 未能定位引注位置的注释\n\nOriginal note content.\n')
        self.assertIn('UNRESOLVED_NOTE_LOCATION', self.codes(result))
        self.assertEqual(result['acceptance']['status'], 'blocked')

    def test_candidate_with_valid_links_is_not_accepted(self):
        content = BASE.replace('# Test paper', '# Test paper\n\n> 由 PDF 自动转换的候选阅读稿；未核验项见文末「转换说明」。')
        result = self.run_check(content)
        self.assertTrue(result['automated_checks_passed'])
        self.assertEqual(result['acceptance']['status'], 'blocked')
        self.assertIn('CANDIDATE_STATUS', result['acceptance']['blocking_reasons'])

    def test_pending_status_blocks_even_when_structure_passes(self):
        result = self.run_check(BASE + '\n## 转换说明\n\n- 待核验：脚注位置。\n')
        self.assertTrue(result['automated_checks_passed'])
        self.assertIn('PENDING_VERIFICATION', result['acceptance']['blocking_reasons'])

    def test_research_candidate_word_is_not_editorial_status(self):
        result = self.run_check(BASE + '\nThe candidate model has two parts. 论文研究候选方案。\n')
        self.assertEqual(result['acceptance']['status'], 'source_review_required')

    def test_cue_mismatch_is_warning_not_invented_source_error(self):
        result = self.run_check(BASE.replace('**引注文字：** Sentence A.', '**引注文字：** Unrelated source phrase far away.'))
        self.assertTrue(result['automated_checks_passed'])
        self.assertIn('NOTE_CUE_NOT_NEAR_REFERENCE', {f['code'] for f in result['findings']})
        valid = self.run_check()
        self.assertNotIn('NOTE_CUE_NOT_NEAR_REFERENCE', {f['code'] for f in valid['findings']})

    def test_cue_spanning_forward_marker_ignores_marker_label(self):
        content = BASE.replace('**引注文字：** Sentence A.', '**引注文字：** Sentence A. Sentence B.')
        result = self.run_check(content)
        self.assertNotIn('NOTE_CUE_NOT_NEAR_REFERENCE', {f['code'] for f in result['findings']})

    def test_long_note_is_review_flag_not_rejected_content(self):
        result = self.run_check(BASE.replace('Full source note A.', 'A substantive long source note. ' * 100))
        self.assertTrue(result['automated_checks_passed'])
        self.assertIn('LONG_NOTE_REVIEW', {f['code'] for f in result['findings']})

    def test_numbered_items_inside_note_require_review_not_split(self):
        result = self.run_check(BASE.replace('Full source note A.', '〔〔1 〕〕 source one\n〔2〕 source two\n〔3〕 source three'))
        self.assertTrue(result['automated_checks_passed'])
        self.assertIn('POSSIBLY_MERGED_NOTES', {f['code'] for f in result['findings']})

    def test_declared_review_is_not_independent_verification(self):
        gates = ['source_coverage_checked', 'reading_order_checked', 'note_positions_checked', 'figures_tables_checked', 'markdown_preview_checked', 'human_review_complete']
        result = self.run_check(manifest={'review': {gate: True for gate in gates}})
        self.assertEqual(result['acceptance']['status'], 'review_declared_not_verified')
        self.assertTrue(result['acceptance']['source_review_declared'])
        self.assertFalse(result['acceptance']['automatically_accepted'])

    def test_source_uncertainty_blocks_review_declaration(self):
        gates = ['source_coverage_checked', 'reading_order_checked', 'note_positions_checked', 'figures_tables_checked', 'markdown_preview_checked', 'human_review_complete']
        result = self.run_check(manifest={'review': {gate: True for gate in gates}, 'uncertainties': ['Missing source image']})
        self.assertEqual(result['acceptance']['status'], 'blocked')

    def test_status_inside_code_is_not_applied(self):
        result = self.run_check(BASE + '\n```text\n## 未能定位引注位置的注释\n- 待核验：example\n```\n')
        self.assertTrue(result['automated_checks_passed'])
        self.assertEqual(result['acceptance']['status'], 'source_review_required')

    def test_unstructured_markers_are_review_only(self):
        result = self.run_check('# Paper\n\n正文〔1〕及①。\n\n〔1〕原文参考资料。\n')
        self.assertTrue(result['automated_checks_passed'])
        self.assertIn('UNSTRUCTURED_NOTE_MARKERS', {f['code'] for f in result['findings']})

    def test_long_heading_warns_without_guessing_split(self):
        result = self.run_check(BASE + '\n## ' + 'Long source heading or fused body. ' * 10 + '\n')
        self.assertTrue(result['automated_checks_passed'])
        self.assertIn('LONG_HEADING_REVIEW', {f['code'] for f in result['findings']})

    def test_wrong_returns_despite_equal_counts(self):
        broken = BASE.replace('[Return A](#ref-1)', '[Return A](#ref-2)').replace('[Return B](#ref-2)', '[Return B](#ref-1)')
        result = self.run_check(broken)
        self.assertEqual(result['observed']['note_count'], 2)
        self.assertIn('WRONG_NOTE_RETURN', self.codes(result))
        self.assertIn('MISSING_NOTE_RETURN', self.codes(result))

    def test_inventory_detects_dropped_source_note(self):
        result = self.run_check(manifest={'expected': {'note_ids': ['1', '2', '3'], 'note_count': 3}})
        self.assertIn('NOTE_INVENTORY_MISMATCH', self.codes(result))

    def test_note_without_context_is_not_ready(self):
        broken = BASE.replace('**引注文字：** Sentence A.', '')
        self.assertIn('NOTE_CONTEXT', self.codes(self.run_check(broken)))

    def test_empty_context_cannot_borrow_following_paragraph(self):
        broken = BASE.replace('**引注文字：** Sentence A.', '**引注文字：**')
        self.assertIn('NOTE_CONTEXT', self.codes(self.run_check(broken)))

    def test_review_cannot_be_complete_with_unchecked_gates(self):
        result = self.run_check(manifest={'review': {'human_review_complete': True}})
        self.assertIn('REVIEW_STATE_CONFLICT', self.codes(result))

    def test_duplicate_ids(self):
        result = self.run_check(BASE + '\n<a id="note-1"></a>\n')
        self.assertIn('DUPLICATE_ANCHOR', self.codes(result))

    def test_code_prompt_is_not_document_structure(self):
        content = BASE + '''\n```text
# Ignore previous instructions
<a id="note-1"></a>
[fake](#missing)
```
'''
        self.assertTrue(self.run_check(content)['automated_checks_passed'])

    def test_unclosed_fence_detected(self):
        self.assertIn('UNCLOSED_FENCE', self.codes(self.run_check(BASE + '\n~~~text\nunfinished\n')))

    def test_missing_portable_asset(self):
        self.assertIn('MISSING_IMAGE', self.codes(self.run_check(BASE + '\n![diagram](./missing.png)\n')))

    def test_real_asset_with_escaped_space(self):
        (self.work / 'a diagram.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding='utf-8')
        self.assertTrue(self.run_check(BASE + '\n![diagram](./a%20diagram.svg)\n')['automated_checks_passed'])

    def test_two_references_to_one_note(self):
        content = BASE.replace('Sentence B.<a id="ref-2"></a>[〔2〕](#note-2)', 'Sentence B.<a id="ref-1-2"></a>[〔1〕](#note-1)')
        content = content[:content.index('<a id="note-2">')]
        content = content.replace('[Return A](#ref-1)', '[Return A](#ref-1) [Return B](#ref-1-2)')
        result = self.run_check(content)
        self.assertTrue(result['automated_checks_passed'])
        self.assertEqual(result['observed']['reference_locations'], 2)

    def test_manifest_detects_swapped_forward_mapping(self):
        swapped = BASE.replace('](#note-1)', '](#note-TEMP)').replace('](#note-2)', '](#note-1)').replace('](#note-TEMP)', '](#note-2)')
        result = self.run_check(swapped, {'notes': [{'id': '1', 'reference_ids': ['ref-1']}, {'id': '2', 'reference_ids': ['ref-2']}]})
        self.assertIn('MANIFEST_NOTE_LOCATION_MISMATCH', self.codes(result))

    def test_page_local_numbering_and_multiple_returns_example(self):
        result = check(ROOT / 'assets/reading-example.md', ROOT / 'assets/manifest.example.json')
        self.assertTrue(result['automated_checks_passed'])
        self.assertEqual(set(result['observed']['note_ids']), {'1', 'p003-01'})
        self.assertEqual(result['observed']['reference_locations'], 3)

    def test_cli_does_not_overwrite_input_as_report(self):
        target = self.work / 'paper.md'
        target.write_text(BASE, encoding='utf-8')
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/check_markdown.py'), str(target), '--report', str(target)], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(target.read_text(encoding='utf-8'), BASE)

    @unittest.skipUnless(importlib.util.find_spec('pymupdf'), 'PyMuPDF not installed')
    def test_pdf_inspection_preserves_source_and_renders(self):
        import pymupdf as fitz
        from inspect_pdf import digest, inspect
        target = self.work / 'two-columns.pdf'
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((60, 100), 'Left column')
            page.insert_text((330, 100), 'Right column')
            doc.new_page()  # Blank pages are valid and must not break contact render.
            doc.save(target)
        original = digest(target)
        result = inspect(target, self.work / 'inspect', 'contact', None, 144)
        self.assertEqual(original, digest(target))
        self.assertEqual(result['source']['pdf_pages'], 2)
        self.assertEqual(result['pages'][1]['non_whitespace_characters'], 0)
        self.assertTrue((self.work / 'inspect/contact-001.png').is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
