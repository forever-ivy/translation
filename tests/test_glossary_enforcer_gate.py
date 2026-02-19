#!/usr/bin/env python3

import unittest

from scripts.openclaw_translation_orchestrator import _validate_glossary_enforcer


class GlossaryEnforcerGateTest(unittest.TestCase):
    def test_glossary_enforcer_flags_missing_terms_per_unit(self):
        context = {
            "format_preserve": {
                "docx_template": {
                    "units": [
                        {"id": "p:1", "text": "هذا مدرسة"},
                    ]
                },
                "xlsx_sources": [
                    {
                        "file": "file.xlsx",
                        "cell_units": [
                            {"file": "file.xlsx", "sheet": "Sheet1", "cell": "A1", "text": "مدرسة"},
                        ],
                    }
                ],
            },
            "glossary_enforcer": {
                "enabled": True,
                "terms": [{"ar": "مدرسة", "en": "School"}],
            },
        }
        draft = {
            "docx_translation_map": [{"id": "p:1", "text": "This is a house"}],
            "xlsx_translation_map": [{"file": "file.xlsx", "sheet": "Sheet1", "cell": "A1", "text": "House"}],
        }

        findings, meta = _validate_glossary_enforcer(context, draft)
        self.assertTrue(meta.get("enabled"))
        self.assertGreaterEqual(meta.get("violations", 0), 2)
        self.assertTrue(any(f.startswith("glossary_enforcer_missing:docx:p:1") for f in findings))
        self.assertTrue(any("xlsx:file.xlsx:Sheet1!A1" in f for f in findings))

    def test_glossary_enforcer_passes_when_term_present(self):
        context = {
            "format_preserve": {
                "docx_template": {
                    "units": [
                        {"id": "p:1", "text": "هذا مدرسة"},
                    ]
                }
            },
            "glossary_enforcer": {
                "enabled": True,
                "terms": [{"ar": "مدرسة", "en": "School"}],
            },
        }
        draft = {
            "docx_translation_map": [{"id": "p:1", "text": "This is a School"}],
        }
        findings, meta = _validate_glossary_enforcer(context, draft)
        self.assertTrue(meta.get("enabled"))
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()

