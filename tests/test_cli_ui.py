import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from prompt_toolkit.document import Document

from cli_ui import (
    COMMANDS,
    _OPEN_COMMAND_PALETTE,
    AdaptivePromptSession,
    SlashCommandCompleter,
    TerminalUI,
    match_commands,
)


class SlashCommandCompleterTests(unittest.TestCase):
    def setUp(self):
        self.completer = SlashCommandCompleter()

    def complete(self, text: str) -> list[str]:
        return [
            item.text
            for item in self.completer.get_completions(Document(text), None)
        ]

    def test_prefix_completion(self):
        self.assertEqual(self.complete("/li"), ["/list"])

    def test_fuzzy_completion_keeps_character_order(self):
        self.assertIn("/delete", self.complete("/dlt"))

    def test_normal_chat_and_command_arguments_do_not_open_menu(self):
        self.assertEqual(self.complete("分析贵州茅台"), [])
        self.assertEqual(self.complete("/title 茅台分析"), [])

    def test_help_lists_commands_and_shortcuts(self):
        help_text = TerminalUI.help_text()
        self.assertIn("/list", help_text)
        self.assertIn("Alt+Enter", help_text)


class TerminalUITests(unittest.TestCase):
    def test_slash_palette_returns_selected_command(self):
        ui = object.__new__(TerminalUI)
        ui.session = Mock()
        ui.session.prompt.return_value = _OPEN_COMMAND_PALETTE
        ui.choose_command = Mock(return_value="/list")

        self.assertEqual(ui.prompt(), "/list")
        ui.choose_command.assert_called_once_with()

    def test_title_command_returns_to_input_with_prefix(self):
        ui = object.__new__(TerminalUI)
        ui.session = Mock()
        ui.session.prompt.side_effect = [_OPEN_COMMAND_PALETTE, "/title 茅台复盘"]
        ui.choose_command = Mock(return_value="/title")

        self.assertEqual(ui.prompt(), "/title 茅台复盘")
        self.assertEqual(ui.session.prompt.call_args_list[1].kwargs["default"], "/title ")

    def test_command_palette_starts_with_every_documented_command(self):
        self.assertEqual(match_commands("/"), list(COMMANDS))

    def test_command_palette_filters_as_user_types(self):
        self.assertEqual(match_commands("/li"), ["/list"])
        self.assertIn("/delete", match_commands("/dlt"))
        self.assertEqual(match_commands("/not-a-command"), [])

    def test_command_palette_cursor_starts_after_slash(self):
        query_input = TerminalUI._create_command_query()

        self.assertEqual(query_input.text, "/")
        self.assertEqual(query_input.buffer.cursor_position, 1)

    def test_prompt_erases_temporary_input_after_submit(self):
        """提交后的输入控件由 prompt-toolkit 清除，避免旧提示符残留。"""
        with TemporaryDirectory() as temp_dir, patch("cli_ui.AdaptivePromptSession") as session:
            TerminalUI(Path(temp_dir) / "history", "test-model")

        self.assertTrue(session.call_args.kwargs["erase_when_done"])
        self.assertTrue(session.call_args.kwargs["show_frame"])
        self.assertNotIn("placeholder", session.call_args.kwargs)
        self.assertEqual(session.call_args.kwargs["reserve_space_for_menu"], 0)

    def test_input_height_follows_explicit_lines(self):
        self.assertEqual(AdaptivePromptSession.calculate_input_height("一行", 80), 1)
        self.assertEqual(AdaptivePromptSession.calculate_input_height("一行\n二行", 80), 2)

    def test_input_height_counts_wrapped_chinese_text(self):
        # 终端共 20 列，扣除边框和提示符后，七个中文字符需要折成两行。
        self.assertEqual(AdaptivePromptSession.calculate_input_height("中文中文中文中", 20), 2)

    def test_input_height_shrinks_and_is_capped(self):
        long_text = "\n".join(str(index) for index in range(10))
        self.assertEqual(AdaptivePromptSession.calculate_input_height(long_text, 80), 6)
        self.assertEqual(AdaptivePromptSession.calculate_input_height("删回一行", 80), 1)

    def test_user_message_is_echoed_once(self):
        output = StringIO()
        with redirect_stdout(output):
            TerminalUI.echo_user_message("第一行\n第二行")

        self.assertEqual(output.getvalue().count("你 ›"), 1)
        self.assertIn("第一行", output.getvalue())
        self.assertIn("第二行", output.getvalue())

    def test_full_screen_is_cleared_after_dialog_returns(self):
        """选择窗口退出后必须清屏，避免 Windows 终端重复显示旧画面。"""
        app = Mock()
        app.run.return_value = "conversation-id"

        with patch("cli_ui.clear") as clear_screen:
            result = TerminalUI._run_full_screen(app)

        self.assertEqual(result, "conversation-id")
        clear_screen.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
