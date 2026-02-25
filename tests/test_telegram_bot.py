#!/usr/bin/env python3

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY

from scripts.telegram_bot import (
    _is_command,
    _load_offset,
    _save_offset,
    handle_update,
    tg_api,
    tg_send,
)


class TelegramBotCommandDetectionTest(unittest.TestCase):
    def test_known_commands(self):
        for cmd in ["new", "run", "status", "ok", "no fix it", "rerun", "approve", "reject"]:
            self.assertTrue(_is_command(cmd), f"{cmd!r} should be a command")

    def test_non_commands(self):
        for text in ["hello", "please translate this", "", "   ", "running late"]:
            self.assertFalse(_is_command(text), f"{text!r} should not be a command")

    def test_case_insensitive(self):
        self.assertTrue(_is_command("NEW"))
        self.assertTrue(_is_command("Run"))
        self.assertTrue(_is_command("STATUS"))


class TelegramBotOffsetTest(unittest.TestCase):
    def test_save_and_load_offset(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            offset_file = Path(td) / "offset"
            with patch("scripts.telegram_bot.OFFSET_FILE", offset_file):
                _save_offset(42)
                self.assertEqual(_load_offset(), 42)

    def test_load_missing_offset(self):
        with patch("scripts.telegram_bot.OFFSET_FILE", Path("/nonexistent/offset")):
            self.assertEqual(_load_offset(), 0)


class TelegramBotTgApiTest(unittest.TestCase):
    @patch("scripts.telegram_bot.urllib.request.urlopen")
    def test_tg_api_success(self, mock_urlopen):
        resp_data = {"ok": True, "result": []}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = tg_api("getUpdates", bot_token="fake:token", timeout=1)
        self.assertTrue(result["ok"])
        mock_urlopen.assert_called_once()

    def test_tg_api_no_token(self):
        with patch("scripts.telegram_bot.BOT_TOKEN", ""):
            with self.assertRaises(RuntimeError):
                tg_api("getUpdates")

    @patch("scripts.telegram_bot.urllib.request.urlopen")
    def test_tg_send_truncates(self, mock_urlopen):
        resp_data = {"ok": True, "result": {"message_id": 1}}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        long_text = "x" * 5000
        tg_send("123", long_text, bot_token="fake:token")
        call_args = mock_urlopen.call_args
        sent_body = json.loads(call_args[0][0].data.decode("utf-8"))
        self.assertLessEqual(len(sent_body["text"]), 4100)


class TelegramBotHandleUpdateTest(unittest.TestCase):
    @patch("scripts.telegram_bot.ALLOWED_CHAT_IDS", {"123"})
    @patch("scripts.telegram_bot._handle_command")
    def test_command_dispatched(self, mock_cmd):
        update = {"message": {"chat": {"id": 123}, "text": "status"}}
        handle_update(update)
        mock_cmd.assert_called_once_with("123", "status")

    @patch("scripts.telegram_bot.ALLOWED_CHAT_IDS", {"123"})
    @patch("scripts.telegram_bot._handle_document")
    def test_document_dispatched(self, mock_doc):
        update = {
            "message": {
                "chat": {"id": 123},
                "text": "",
                "document": {"file_id": "abc", "file_name": "test.docx"},
            }
        }
        handle_update(update)
        mock_doc.assert_called_once_with("123", update["message"])

    @patch("scripts.telegram_bot.ALLOWED_CHAT_IDS", {"999"})
    @patch("scripts.telegram_bot._handle_command")
    @patch("scripts.telegram_bot._handle_text")
    def test_chat_id_filtered(self, mock_text, mock_cmd):
        update = {"message": {"chat": {"id": 123}, "text": "run"}}
        handle_update(update)
        mock_cmd.assert_not_called()
        mock_text.assert_not_called()

    def test_no_message_ignored(self):
        # Should not raise
        handle_update({"update_id": 1})

    @patch("scripts.telegram_bot.ALLOWED_CHAT_IDS", {"123"})
    @patch("scripts.telegram_bot._handle_text")
    def test_plain_text_dispatched(self, mock_text):
        update = {"message": {"chat": {"id": 123}, "text": "hello world"}}
        handle_update(update)
        mock_text.assert_called_once_with("123", "hello world")


class TelegramBotPollLoopTest(unittest.TestCase):
    @patch("scripts.telegram_bot._release_pid_lock")
    @patch("scripts.telegram_bot._acquire_pid_lock", return_value=True)
    @patch("scripts.telegram_bot._save_offset")
    @patch("scripts.telegram_bot._load_offset", return_value=0)
    @patch("scripts.telegram_bot.time.sleep")
    @patch("scripts.telegram_bot.tg_api")
    def test_409_conflict_retries_instead_of_exit(
        self,
        mock_tg_api,
        _mock_sleep,
        _mock_load_offset,
        _mock_save_offset,
        _mock_acquire_lock,
        _mock_release_lock,
    ):
        import scripts.telegram_bot as bot

        def stop_after_first_update(_update):
            bot._running = False

        mock_tg_api.side_effect = [
            {"ok": False, "error_code": 409, "description": "Conflict"},
            {"ok": True, "result": [{"update_id": 10, "message": {"chat": {"id": 123}, "text": "status"}}]},
        ]

        with patch("scripts.telegram_bot.handle_update", side_effect=stop_after_first_update):
            bot._running = True
            code = bot.poll_loop()

        self.assertEqual(code, 0)
        self.assertEqual(mock_tg_api.call_count, 2)


if __name__ == "__main__":
    unittest.main()
