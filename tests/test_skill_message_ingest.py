#!/usr/bin/env python3

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.skill_message_ingest import _save_attachment_to_path


class _FakeResponse:
    def __init__(self, chunks: list[bytes], headers: dict[str, str] | None = None):
        self._chunks = list(chunks)
        self.headers = headers or {}

    def read(self, _n: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class SkillMessageIngestAttachmentTest(unittest.TestCase):
    @patch("scripts.skill_message_ingest.urllib.request.urlopen")
    def test_save_attachment_downloads_media_url(self, mocked_urlopen):
        mocked_urlopen.return_value = _FakeResponse([b"DATA"])
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "file.xlsx"
            ok, reason = _save_attachment_to_path(
                {"mediaUrl": "https://example.com/file.xlsx"},
                target_path=target,
            )
            self.assertTrue(ok)
            self.assertEqual(reason, "downloaded_url")
            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), b"DATA")

    @patch("scripts.skill_message_ingest.urllib.request.urlopen")
    def test_save_attachment_blocks_unsupported_suffix_for_download(self, mocked_urlopen):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "file.txt"
            ok, reason = _save_attachment_to_path(
                {"mediaUrl": "https://example.com/file.txt"},
                target_path=target,
            )
            self.assertFalse(ok)
            self.assertIn("download_blocked_suffix", reason)
            mocked_urlopen.assert_not_called()

    @patch("scripts.skill_message_ingest.urllib.request.urlopen")
    def test_save_attachment_fails_fast_when_content_length_too_large(self, mocked_urlopen):
        mocked_urlopen.return_value = _FakeResponse([b""], headers={"Content-Length": str(1024 * 1024 + 1)})
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "file.xlsx"
            with patch.dict("os.environ", {"OPENCLAW_ATTACHMENT_DOWNLOAD_MAX_MB": "1"}, clear=False):
                ok, reason = _save_attachment_to_path(
                    {"mediaUrl": "https://example.com/file.xlsx"},
                    target_path=target,
                )
        self.assertFalse(ok)
        self.assertEqual(reason, "download_too_large")

    def test_save_attachment_rejects_invalid_base64(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "file.docx"
            ok, reason = _save_attachment_to_path(
                {"content_base64": "!!!not_base64!!!"},
                target_path=target,
            )
            self.assertFalse(ok)
            self.assertEqual(reason, "invalid_base64")

    def test_save_attachment_rejects_base64_over_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "file.xlsx"
            data = b"a" * (1024 * 1024 + 1)
            encoded = base64.b64encode(data).decode("utf-8")
            with patch.dict("os.environ", {"OPENCLAW_ATTACHMENT_DOWNLOAD_MAX_MB": "1"}, clear=False):
                ok, reason = _save_attachment_to_path(
                    {"content_base64": encoded},
                    target_path=target,
                )
            self.assertFalse(ok)
            self.assertEqual(reason, "payload_too_large")


if __name__ == "__main__":
    unittest.main()
