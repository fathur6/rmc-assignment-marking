"""Keyless smoke tests for the RMC marking engine."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pymupdf

from engine.annotate import annotate_pdf
from engine.config import Config, MODULE_ROOT
from engine.extract import extract_pages, normalize_text, resolve_anchor
from engine.form import fill_form
from engine.grade import check_text, grade, validate_payload
from engine.mark import folder_for, safe_name
from engine.report import write_review_html
from engine.rubric import band_for, compute


def make_manuscript(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 90), "PPS10203 Research Methodology SEM II 2025/2026", fontsize=12)
    page.insert_text((72, 130), "An Analytical Study on Small Business Failure", fontsize=14)
    page.insert_text((72, 170), "Small businesses face high failure rates worldwide.", fontsize=11)
    page.insert_text((72, 190), "Several studies have addressed the topic.", fontsize=11)
    page2 = doc.new_page(width=595, height=842)
    page2.insert_text((72, 90), "Methodology", fontsize=13)
    page2.insert_text((72, 120), "This study adopts a descriptive analytical approach.", fontsize=11)
    doc.set_metadata({"title": "Test proposal"})
    doc.save(path)
    doc.close()


def dummy_payload() -> dict:
    return {
        "meta": {"name": "Test Student", "matric": "SI9999", "title": "An Analytical Study"},
        "criteria": {
            "clo2_org": {"score": 6, "band": "Good", "evidence": "p1", "rationale": "ok"},
            "clo2_align": {"score": 5, "band": "Satisfactory", "evidence": "p1", "rationale": "ok"},
            "clo2_lit": {"score": 5, "band": "Satisfactory", "evidence": "p2", "rationale": "ok"},
            "clo3_integrity": {"score": 6, "band": "Good", "evidence": "p1", "rationale": "ok"},
            "clo3_ethics": {"score": 5, "band": "Satisfactory", "evidence": "p2", "rationale": "ok"},
        },
        "comments": [],
        "strengths": ["Structure is complete."],
        "improvements": ["Narrow the title."],
        "confidence": 0.9,
        "needs_review": False,
        "review_reasons": [],
    }


class RubricTests(unittest.TestCase):
    def test_bands(self) -> None:
        self.assertEqual(band_for(0), "Very weak")
        self.assertEqual(band_for(3), "Weak")
        self.assertEqual(band_for(5), "Satisfactory")
        self.assertEqual(band_for(8), "Good")
        self.assertEqual(band_for(10), "Excellent")

    def test_compute_matches_official_sample(self) -> None:
        result = compute(
            {"clo2_org": 6, "clo2_align": 5, "clo2_lit": 5, "clo3_integrity": 6, "clo3_ethics": 5}
        )
        self.assertEqual(result["clo2_raw"], 16)
        self.assertEqual(result["clo3_raw"], 11)
        self.assertEqual(result["total"], 32.33)
        self.assertEqual(result["percent"], 53.9)

    def test_compute_rejects_missing(self) -> None:
        with self.assertRaises(ValueError):
            compute({"clo2_org": 6})


class ExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.pdf = Path(self.tmp.name) / "manuscript.pdf"
        make_manuscript(self.pdf)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_normalize_removes_invisibles(self) -> None:
        self.assertEqual(normalize_text("2\ufe0f01\ufe0f4\ufe0f"), "2014")
        self.assertEqual(normalize_text("  a   b  "), "a b")

    def test_anchor_across_lines(self) -> None:
        pages = extract_pages(self.pdf)
        self.assertEqual(len(pages), 2)
        match = pages[0].find("Small businesses face high failure rates")
        self.assertIsNotNone(match)
        rects = pages[0].highlight_rects(match)
        self.assertTrue(rects)
        resolved = resolve_anchor(pages, 2, "Small businesses face high failure rates")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved[0], 1)

    def test_missing_anchor_returns_none(self) -> None:
        pages = extract_pages(self.pdf)
        self.assertIsNone(pages[0].find("this phrase does not exist anywhere"))


class ArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.pdf = Path(self.tmp.name) / "manuscript.pdf"
        make_manuscript(self.pdf)
        self.pages = extract_pages(self.pdf)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_annotate_writes_notes_and_highlights(self) -> None:
        target = Path(self.tmp.name) / "annotated.pdf"
        comments = [
            {
                "page": 1,
                "anchor": "Small businesses face high failure rates",
                "clo": "CLO2",
                "category": "Title",
                "comment": "Narrow the population and define failure operationally.",
                "order": 0,
            },
            {
                "page": 2,
                "anchor": "descriptive analytical approach",
                "clo": "CLO2",
                "category": "Methodology",
                "comment": "Name and justify a recognised design and sampling plan.",
                "order": 1,
            },
        ]
        stats = annotate_pdf(self.pdf, target, comments, "Evaluator")
        self.assertEqual(stats.placed, 2)
        self.assertEqual(stats.unmatched, [])
        doc = pymupdf.open(target)
        notes = [a for a in (doc[0].annots() or []) if a.type[1] == "Text"]
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].info["content"].startswith("[CLO2 - Title]"))
        self.assertEqual(notes[0].info["title"], "Evaluator")
        doc.close()

    def test_form_fills_two_pages(self) -> None:
        target = Path(self.tmp.name) / "form.pdf"
        payload = dummy_payload()
        computed = fill_form(
            MODULE_ROOT / "assets" / "marking-form.pdf",
            target,
            {"student_name": "Test Student", "matric_number": "SI9999", "title": "Study"},
            payload,
            "Evaluator",
            "SEM II 2025/2026",
        )
        self.assertEqual(computed["total"], 32.33)
        doc = pymupdf.open(target)
        self.assertEqual(doc.page_count, 2)
        text = doc[1].get_text()
        self.assertIn("Test Student", text)
        self.assertIn("32.33 / 60", text)
        doc.close()

    def test_validate_payload_corrects_page_and_filters_short(self) -> None:
        config = Config()
        payload = {
            "student_name": "",
            "matric_number": "",
            "title": "Study",
            "criteria": {
                "clo2_org": {"score": 6, "evidence": "p1", "rationale": "ok"},
                "clo2_align": {"score": 5, "evidence": "p1", "rationale": "ok"},
                "clo2_lit": {"score": 5, "evidence": "p1", "rationale": "ok"},
                "clo3_integrity": {"score": 6, "evidence": "p1", "rationale": "ok"},
                "clo3_ethics": {"score": 5, "evidence": "p1", "rationale": "ok"},
            },
            "comments": [
                {
                    "page": 9,
                    "anchor": "descriptive analytical approach",
                    "clo": "CLO2",
                    "category": "Methodology",
                    "comment": "Name and justify a recognised design and sampling plan.",
                },
                {"page": 1, "anchor": "x", "comment": "too short"},
            ],
            "strengths": ["Complete structure."],
            "improvements": ["Narrow the title."],
            "confidence": 0.8,
        }
        source = Path(self.tmp.name) / "SI9999 - Test Student assignment.pdf"
        normalized, issues = validate_payload(payload, self.pages, config, source)
        self.assertEqual(normalized["comments"][0]["page"], 2)
        self.assertEqual(normalized["meta"]["matric"], "SI9999")
        self.assertEqual(len(normalized["comments"]), 1)
        self.assertTrue(any("page corrected" in issue for issue in issues))


class GradeGuardTests(unittest.TestCase):
    def test_missing_identity_flags_review(self) -> None:
        payload = dummy_payload()
        payload["meta"] = {"name": "", "matric": "", "title": "Study"}
        normalized, issues = validate_payload(
            {
                "student_name": "",
                "matric_number": "",
                "title": "Study",
                "criteria": {
                    "clo2_org": {"score": 6},
                    "clo2_align": {"score": 5},
                    "clo2_lit": {"score": 5},
                    "clo3_integrity": {"score": 6},
                    "clo3_ethics": {"score": 5},
                },
                "comments": [],
                "strengths": ["Structure."],
                "improvements": ["Narrow title."],
                "confidence": 0.9,
            },
            extract_pages(MODULE_ROOT / "assets" / "marking-form.pdf"),
            Config(),
            Path("manuscript.pdf"),
        )
        self.assertTrue(normalized["needs_review"])
        self.assertTrue(any("name" in reason for reason in normalized["review_reasons"]))

    def test_malay_proposal_accepted(self) -> None:
        text = (
            "1. Latar Belakang Penyelidikan\n"
            "2. Pernyataan Masalah\n"
            "3. Kepentingan Penyelidikan\n"
            "4. Tujuan Penelitian\n"
            "5. Ulasan Penyelidikan Terdahulu\n"
            "6. Metodologi\n"
            "9. Bibliografi\n"
            + ("Kajian ini membincangkan hubungan antara teks dan konteks. " * 40)
        )
        check_text(text, Config())

    def test_thesis_rejected(self) -> None:
        text = (
            "METODOLOGI PENDALILAN AKIDAH\n"
            "Tesis yang dikemukakan bagi memenuhi syarat untuk memperoleh "
            "Doktor Falsafah Pengajian Islam\n"
            + ("Kandungan tesis ini membincangkan metodologi. " * 60)
        )
        with self.assertRaises(ValueError):
            check_text(text, Config())

    def test_image_only_pdf_is_refused_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blank = Path(tmp) / "scanned.pdf"
            doc = pymupdf.open()
            doc.new_page()
            doc.save(blank)
            doc.close()
            with self.assertRaises(ValueError):
                grade(Config(), blank)


    def test_non_proposal_text_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "form.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            for index in range(30):
                page.insert_text(
                    (72, 60 + index * 14),
                    "Problem statement Objectives Methodology marking criteria row",
                    fontsize=9,
                )
            doc.save(path)
            doc.close()
            with self.assertRaises(ValueError):
                grade(Config(), path)


class NamingTests(unittest.TestCase):
    def test_safe_name(self) -> None:
        self.assertEqual(safe_name('A/B: "C"'), "A-B- -C")

    def test_folder_pattern(self) -> None:
        config = Config()
        self.assertEqual(folder_for(config, "SI1", "Jane Doe", "x"), "SI1 - Jane Doe")


class ReportTests(unittest.TestCase):
    def test_empty_gradebook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = write_review_html(tmp)
            self.assertTrue(target.exists())
            self.assertIn("No manuscripts", target.read_text())


if __name__ == "__main__":
    unittest.main()
