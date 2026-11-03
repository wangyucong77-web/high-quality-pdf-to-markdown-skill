#!/usr/bin/env python3
"""Behavior tests using only disposable input/output directories."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from audit_directory import audit, digest, missing_windows, normalize, visible_text

try:
    import pymupdf
except ImportError:
    pymupdf = None


class BatchAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdfs = self.root / 'papers'
        self.md = self.pdfs / 'markdown'
        self.md.mkdir(parents=True)
        self.output = self.root / 'work'

    def tearDown(self):
        self.temp.cleanup()

    def run_audit(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return audit(self.md, self.pdfs, self.output, **kwargs)

    def test_missing_paragraph_is_candidate_but_not_declared_omission(self):
        kept = 'The documented process begins with a careful classification of source material.'
        lost = 'A completely different paragraph describes the legal duties of the individual owner.'
        candidates = missing_windows(kept + '\n' + lost, normalize(kept))
        self.assertTrue(any('legal' in c['normalized_excerpt'] for c in candidates))
        self.assertLessEqual(len(candidates), 3)

    def test_url_autolinks_preserve_source_text_windows(self):
        source = ('Source details are available at https://example.org/research/long-paper-2026 '
                  'and http://example.org/archive/2025; contact mailto:editor@example.org for metadata.')
        markdown = ('Source details are available at <https://example.org/research/long-paper-2026> '
                    'and <http://example.org/archive/2025>; contact <mailto:editor@example.org> for metadata.')
        self.assertEqual(missing_windows(source, normalize(visible_text(markdown))), [])

    @unittest.skipUnless(pymupdf, 'PyMuPDF unavailable')
    def test_entire_missing_page_and_readonly_hashes(self):
        paper = self.pdfs / 'paper.pdf'
        with pymupdf.open() as doc:
            doc.new_page().insert_text((72, 72), 'The first page has detailed substantive material that is retained.')
            doc.new_page().insert_text((72, 72), 'The absent second page describes obligations that cannot be silently omitted.')
            doc.save(paper)
        md = self.md / 'paper.md'
        md.write_text('# Paper\n\nThe first page has detailed substantive material that is retained.', encoding='utf-8')
        before = (digest(md), digest(paper))
        summary = self.run_audit()
        evidence = json.loads((self.output / summary['files'][0]['evidence']).read_text(encoding='utf-8'))
        self.assertEqual(evidence['source_pages'], 2)
        self.assertIn(2, [p['pdf_page'] for p in evidence['page_candidates']])
        self.assertEqual(before, (digest(md), digest(paper)))

    @unittest.skipUnless(pymupdf, 'PyMuPDF unavailable')
    def test_reordered_matched_paragraphs_never_automatically_pass(self):
        a = 'The first ordered paragraph explains how the process begins and why it matters.'
        b = 'The second ordered paragraph provides a conclusion only after the earlier premise.'
        with pymupdf.open() as doc:
            doc.new_page().insert_text((72, 72), a)
            doc.new_page().insert_text((72, 72), b)
            doc.save(self.pdfs / 'paper.pdf')
        (self.md / 'paper.md').write_text('# Paper\n\n' + b + '\n\n' + a, encoding='utf-8')
        summary = self.run_audit()
        evidence = json.loads((self.output / summary['files'][0]['evidence']).read_text(encoding='utf-8'))
        self.assertFalse(summary['automatically_accepted'])
        self.assertFalse(evidence['reading_order_verified'])
        self.assertFalse(evidence['source_fidelity_verified'])
        self.assertEqual(evidence['page_candidates'], [])

    def test_source_missing_not_guessed_from_similar_name(self):
        (self.md / 'paper.md').write_text('# Paper\n\nBody.', encoding='utf-8')
        (self.pdfs / 'paper-final.pdf').write_bytes(b'not used')
        summary = self.run_audit(structure_only=True)
        self.assertEqual(summary['files'][0]['source_status'], 'missing')
        self.assertIsNone(summary['files'][0]['source_sha256'])

    def test_cli_refuses_input_or_populated_output(self):
        original = self.md / 'paper.md'
        original.write_text('# Original\n', encoding='utf-8')
        for output in (original, self.md, self.md / 'work', self.pdfs):
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('audit_directory.py')),
                                     str(self.md), '--out', str(output), '--structure-only'], capture_output=True)
            self.assertEqual(result.returncode, 2)
        self.assertEqual(original.read_text(encoding='utf-8'), '# Original\n')
        self.output.mkdir()
        sentinel = self.output / 'summary.json'
        sentinel.write_text('do not replace', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.run_audit(structure_only=True)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'do not replace')

    def test_structure_only_stdlib_and_unreadable_image_evidence(self):
        (self.md / 'paper.md').write_text('# Paper\n\n![diagram](missing.png)', encoding='utf-8')
        # -S excludes site-packages: optional PDF dependencies must not be loaded.
        result = subprocess.run([sys.executable, '-S', str(Path(__file__).with_name('audit_directory.py')),
                                 str(self.md), '--out', str(self.output), '--structure-only'], capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr.decode(errors='replace'))
        summary = json.loads((self.output / 'summary.json').read_text(encoding='utf-8'))
        evidence = json.loads((self.output / summary['files'][0]['evidence']).read_text(encoding='utf-8'))
        self.assertEqual(evidence['assets'][0]['status'], 'unreadable')
        self.assertFalse(evidence['automatically_accepted'])


if __name__ == '__main__':
    unittest.main()
