#!/usr/bin/env python3

import json
import unittest

from scripts.gateway_format_contract import apply_format_contract, build_section_format_contract


class GatewayFormatContractTest(unittest.TestCase):
    def test_build_section_format_contract_detects_markers(self):
        prompt = "§1§ first\n§2§ second\n§3§ third"
        contract = build_section_format_contract(prompt)
        self.assertIsNotNone(contract)
        self.assertEqual(contract["mode"], "sectioned_text_ar_en_v1")
        self.assertEqual(contract["expected_sections"], 3)
        self.assertEqual(contract["section_prefix"], "§")

    def test_apply_format_contract_accepts_plain_section_text(self):
        contract = {
            "mode": "sectioned_text_ar_en_v1",
            "expected_sections": 2,
            "section_prefix": "§",
            "forbid_extra_text": True,
            "forbid_markdown_fence": True,
        }
        out = apply_format_contract("§1§ Alpha\n§2§ Beta", contract)
        self.assertTrue(out["ok"])
        self.assertEqual(out["text"], "§1§ Alpha\n§2§ Beta")
        self.assertEqual((out.get("meta") or {}).get("source"), "raw")

    def test_apply_format_contract_extracts_from_json_final_text(self):
        contract = {
            "mode": "sectioned_text_ar_en_v1",
            "expected_sections": 2,
            "section_prefix": "§",
            "forbid_extra_text": True,
            "forbid_markdown_fence": True,
        }
        payload = {
            "final_text": "§1§ One\n§2§ Two",
            "codex_pass": True,
        }
        out = apply_format_contract(json.dumps(payload, ensure_ascii=False), contract)
        self.assertTrue(out["ok"])
        self.assertEqual(out["text"], "§1§ One\n§2§ Two")
        self.assertEqual((out.get("meta") or {}).get("source"), "json.final_text")

    def test_apply_format_contract_rejects_missing_sections(self):
        contract = {
            "mode": "sectioned_text_ar_en_v1",
            "expected_sections": 3,
            "section_prefix": "§",
            "forbid_extra_text": True,
            "forbid_markdown_fence": True,
        }
        out = apply_format_contract("§1§ One\n§2§ Two", contract)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "format_contract_failed")
        self.assertIn("expected_sections", str(out.get("detail") or ""))


if __name__ == "__main__":
    unittest.main()
