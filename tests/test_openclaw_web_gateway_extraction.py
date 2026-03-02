#!/usr/bin/env python3

import unittest

from scripts.openclaw_web_gateway import PlaywrightWebProvider


class OpenClawWebGatewayExtractionTest(unittest.TestCase):
    def test_pick_response_skips_code_block_that_is_prompt_substring(self) -> None:
        prompt = "Rules: JSON only."
        probe = {
            "code_blocks": ["JSON only."],
            "assistant_texts": [],
            "generic_last": "",
            "main_tail": '{"ok":true}',
        }
        text, method = PlaywrightWebProvider._pick_response_text(probe, prompt_text=prompt)
        self.assertEqual(text, '{"ok":true}')
        self.assertEqual(method, "main_tail")

    def test_pick_response_strips_prompt_prefix_from_assistant_text(self) -> None:
        prompt = "Translate and return JSON only."
        assistant_value = prompt + "\n\n" + '{"docx_translation_map":[{"id":"p:1","text":"Hi"}]}'
        probe = {
            "assistant_texts": [assistant_value],
            "code_blocks": [],
            "generic_last": "",
            "main_tail": "",
        }
        text, method = PlaywrightWebProvider._pick_response_text(probe, prompt_text=prompt)
        self.assertEqual(text, '{"docx_translation_map":[{"id":"p:1","text":"Hi"}]}')
        self.assertEqual(method, "assistant")

    def test_pick_response_prefers_assistant_code_blocks(self) -> None:
        prompt = "Return strict JSON only."
        probe = {
            "assistant_code_blocks": ['{"a":1}'],
            "assistant_texts": ["not json"],
            "code_blocks": ['{"b":2}'],
            "generic_last": "",
            "main_tail": "",
        }
        text, method = PlaywrightWebProvider._pick_response_text(probe, prompt_text=prompt)
        self.assertEqual(text, '{"a":1}')
        self.assertEqual(method, "assistant_code_block")

    def test_pick_response_does_not_return_empty_when_assistant_text_present(self) -> None:
        prompt = "Return JSON only."
        probe = {
            "assistant_texts": ['{"ok":true}'],
            "code_blocks": ["JSON only."],
            "generic_last": "",
            "main_tail": "",
        }
        text, method = PlaywrightWebProvider._pick_response_text(probe, prompt_text=prompt)
        self.assertEqual(text, '{"ok":true}')
        self.assertEqual(method, "assistant")


if __name__ == "__main__":
    unittest.main()

