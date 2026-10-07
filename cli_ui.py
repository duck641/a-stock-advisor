"""终端聊天界面组件。

这个模块只负责“怎么输入和选择”，不包含 Agent、数据库或业务逻辑。
把 UI 从 chat.py 拆出来后，命令补全、快捷键和弹窗可以独立测试，
也避免主对话循环继续堆积大量终端细节。
"""

from collections.abc import Callable, Iterable
from pathlib import Path

from prompt_toolkit import Application, PromptSession
from prompt_toolkit.application.current import get_app
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.key_binding.key_bindings import merge_key_bindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.shortcuts import CompleteStyle, clear, yes_no_dialog
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import Dialog, Frame, Label, RadioList, TextArea


# 命令和说明只维护一份：补全菜单与 /help 都读取这里，避免两处文案不一致。
COMMANDS: dict[str, str] = {
    "/help": "显示命令与快捷键帮助",
    "/new": "新建并切换到一个对话",
    "/list": "打开会话选择窗口",
    "/title": "重命名当前会话，用法：/title 名称",
    "/delete": "选择并删除一个会话",
    "/compress": "立即压缩当前会话历史",
    "/quit": "退出终端聊天",
}

NEW_CONVERSATION = "__new_conversation__"
# 这个值只在 PromptSession 和 TerminalUI 之间传递，不会写入输入历史或发送给 Agent。
_OPEN_COMMAND_PALETTE = "__open_command_palette__"
_INPUT_PROMPT = "你 › "


def match_commands(query: str) -> list[str]:
    """按前缀优先、字符顺序其次的规则筛选斜杠命令。"""
    normalized = query.lower()
    if not normalized.startswith("/") or " " in normalized:
        return []
    if normalized == "/":
        # 初始面板沿用 COMMANDS 的人工编排顺序，方便形成稳定的操作记忆。
        return list(COMMANDS)

    def matches(command: str) -> bool:
        chars = iter(command)
        return all(any(candidate == wanted for candidate in chars) for wanted in normalized)

    candidates = [command for command in COMMANDS if matches(command)]
    candidates.sort(
        key=lambda command: (not command.startswith(normalized), len(command), command)
    )
    return candidates


class AdaptivePromptSession(PromptSession[str]):
    """让输入区的高度精确跟随文字实际占用的终端行数。"""

    MAX_INPUT_LINES = 6

    @classmethod
    def calculate_input_height(cls, text: str, terminal_columns: int) -> int:
        """计算显式换行和自动折行后的可见行数。"""
        # Frame 左右边框各占一列；第一行提示符也占用可输入宽度。
        content_columns = max(
            1,
            terminal_columns - 2 - get_cwidth(_INPUT_PROMPT),
        )
        visible_lines = 0
        for line in text.split("\n"):
            # 中文等全角字符在终端中占两列，不能直接使用 Python 的 len()。
            display_width = get_cwidth(line.expandtabs(4))
            visible_lines += max(1, (display_width + content_columns - 1) // content_columns)

        return min(max(visible_lines, 1), cls.MAX_INPUT_LINES)

    def _get_default_buffer_control_height(self) -> Dimension:
        # Window 每次重绘都会调用这里，因此输入、删除和终端缩放都会立即更新高度。
        terminal_columns = get_app().output.get_size().columns
        visible_lines = self.calculate_input_height(
            self.default_buffer.text,
            terminal_columns,
        )
        # 使用精确高度而不是 1～6 的范围，避免父布局自行分配多余空间。
        return Dimension.exact(visible_lines)


class SlashCommandCompleter(Completer):
    """只在输入以 ``/`` 开头时提供命令补全。

    匹配同时支持前缀和字符顺序，例如 ``/li``、``/ls`` 都能找到
    ``/list``。正常聊天内容不会触发命令弹窗。
    """

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        query = document.text_before_cursor.lower()
        if not query.startswith("/") or " " in query:
            return

        # 前缀匹配排在模糊匹配前面，让最符合直觉的结果位于菜单顶部。
        for command in match_commands(query):
            yield Completion(
                command,
                start_position=-len(query),
                display=command,
                display_meta=COMMANDS[command],
            )


CLI_STYLE = Style.from_dict({
    "prompt": "bold #5fd7ff",
    "frame.border": "#5fd7ff",
    "bottom-toolbar": "bg:#20242c #b8c0cc",
    "bottom-toolbar.key": "bg:#20242c bold #5fd7ff",
    "completion-menu.completion": "bg:#20242c #e5e7eb",
    "completion-menu.completion.current": "bg:#005f87 #ffffff bold",
    "completion-menu.meta.completion": "bg:#20242c #9ca3af",
    "completion-menu.meta.completion.current": "bg:#005f87 #ffffff",
    "dialog": "bg:#20242c",
    "dialog frame.label": "bold #5fd7ff",
    "dialog.body": "bg:#20242c #e5e7eb",
    "dialog shadow": "bg:#111318",
    "button": "bg:#3b4048 #ffffff",
    "button.focused": "bg:#005f87 #ffffff bold",
    "radio": "#e5e7eb",
    "radio-selected": "#5fd7ff bold",
    "command-query": "bg:#111318 #ffffff",
    "command-item": "#d1d5db",
    "command-item.selected": "bg:#005f87 #ffffff bold",
    "command-description": "#9ca3af",
    "command-description.selected": "bg:#005f87 #ffffff",
    "command-empty": "#9ca3af italic",
})


class TerminalUI:
    """封装 PromptSession、底部状态栏和终端选择窗口。"""

    def __init__(
        self,
        history_file: str | Path,
        model_name: str,
        conversation_title: str = "未选择",
    ):
        self.model_name = model_name
        self.conversation_title = conversation_title
        self._key_bindings = self._build_key_bindings()

        self.session: PromptSession[str] = AdaptivePromptSession(
            history=FileHistory(str(history_file)),
            completer=SlashCommandCompleter(),
            auto_suggest=AutoSuggestFromHistory(),
            complete_while_typing=True,
            # 命令只有几个，本地同步补全能在按下“/”时立即弹出，不需要后台线程。
            complete_in_thread=False,
            complete_style=CompleteStyle.COLUMN,
            # 斜杠命令现在使用独立弹窗，不再为旧补全菜单固定预留七行。
            reserve_space_for_menu=0,
            enable_history_search=True,
            key_bindings=self._key_bindings,
            bottom_toolbar=self._bottom_toolbar,
            prompt_continuation=self._continuation_prompt,
            mouse_support=True,
            style=CLI_STYLE,
            # PromptSession 默认会把提交过的输入行留在屏幕上。命令打开弹窗后，
            # 下一轮提示符会与旧提示符并排出现，因此先擦除，再由业务层只回显普通消息。
            erase_when_done=True,
            # prompt-toolkit 使用终端字符绘制输入框，窗口缩放时会自动重新计算宽度。
            show_frame=True,
        )

    @staticmethod
    def _build_key_bindings() -> KeyBindings:
        bindings = KeyBindings()

        @bindings.add("c-n")
        def _new_conversation(event) -> None:
            """Ctrl+N 等价于输入 /new 后发送。"""
            buffer = event.current_buffer
            buffer.set_document(Document("/new", cursor_position=4), bypass_readonly=True)
            buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        def _insert_newline(event) -> None:
            """Alt+Enter 插入换行；普通 Enter 仍然发送消息。"""
            event.current_buffer.insert_text("\n")

        @bindings.add("c-l")
        def _clear_screen(event) -> None:
            event.app.renderer.clear()

        @bindings.add("/")
        def _open_command_palette(event) -> None:
            """在空输入框中按 / 打开命令弹窗；正文中的 / 仍按普通字符输入。"""
            if event.current_buffer.text:
                event.current_buffer.insert_text("/")
                return
            # 直接结束本轮临时输入，不调用 Buffer 的提交逻辑，所以弹窗信号不会
            # 被 FileHistory 当作用户输入保存。
            event.app.exit(result=_OPEN_COMMAND_PALETTE)

        return bindings

    def _bottom_toolbar(self) -> FormattedText:
        # 工具栏每次重绘时读取最新标题，因此切换或重命名后无需重建 Session。
        title = self.conversation_title.replace("\n", " ")[:32]
        return FormattedText([
            ("class:bottom-toolbar", f" 会话：{title}  │  模型：{self.model_name}  │  "),
            ("class:bottom-toolbar.key", "Ctrl+N"),
            ("class:bottom-toolbar", " 新建  "),
            ("class:bottom-toolbar.key", "Ctrl+L"),
            ("class:bottom-toolbar", " 清屏  "),
            ("class:bottom-toolbar.key", "Alt+Enter"),
            ("class:bottom-toolbar", " 换行  "),
            ("class:bottom-toolbar.key", "/help"),
            ("class:bottom-toolbar", " 帮助 "),
        ])

    @staticmethod
    def _continuation_prompt(width: int, line_number: int, is_soft_wrap: bool):
        if is_soft_wrap:
            return " " * width
        return FormattedText([("class:prompt", "… ")])

    def update_conversation(self, title: str) -> None:
        self.conversation_title = title

    def prompt(self) -> str:
        default = ""
        while True:
            result = self.session.prompt(
                FormattedText([("class:prompt", _INPUT_PROMPT)]),
                default=default,
            )
            if result != _OPEN_COMMAND_PALETTE:
                return result

            command = self.choose_command()
            if command is None:
                default = ""
                continue
            if command == "/title":
                # /title 还需要用户输入名称，返回输入框并预填命令前缀。
                default = "/title "
                continue
            return command

    @staticmethod
    def echo_user_message(message: str) -> None:
        """把已提交的普通消息显示一次；命令不会调用这里。"""
        lines = message.splitlines() or [""]
        print(f"\n你 › {lines[0]}")
        for line in lines[1:]:
            print(f"     {line}")

    def choose_conversation(
        self,
        conversations: list[dict],
        *,
        title: str,
        current_id: str | None = None,
        allow_new: bool = False,
    ) -> str | None:
        """用方向键选择会话；返回会话 ID、NEW_CONVERSATION 或 None（取消）。"""
        values: list[tuple[str, str]] = []
        if allow_new:
            values.append((NEW_CONVERSATION, "＋ 新建对话"))

        for conversation in conversations:
            current = "  ← 当前" if conversation["id"] == current_id else ""
            label = (
                f"{conversation['title']}  ·  "
                f"{conversation['msg_count']} 条消息{current}"
            )
            values.append((conversation["id"], label))

        # 已有当前会话时默认停在当前项；启动阶段默认停在最近更新的会话。
        default = current_id or (conversations[0]["id"] if conversations else NEW_CONVERSATION)
        return self._run_conversation_dialog(
            title=title,
            values=values,
            default=default,
        )

    def choose_command(self) -> str | None:
        """显示可继续输入并实时过滤的斜杠命令面板。"""
        return self._run_command_palette()

    @staticmethod
    def _create_command_query() -> TextArea:
        """创建以 / 开头且光标位于末尾的命令搜索框。"""
        query_input = TextArea(
            text="/",
            multiline=False,
            prompt=FormattedText([("class:prompt", "筛选 › ")]),
            height=1,
            wrap_lines=False,
            style="class:command-query",
        )
        # TextArea 的初始光标默认可能停在文本开头；显式移到 / 后面，保证用户
        # 接着输入 li 时得到 /li，而不是 li/。
        query_input.buffer.cursor_position = len(query_input.text)
        return query_input

    @staticmethod
    def _run_command_palette() -> str | None:
        """运行命令搜索面板，输入始终留在搜索框中。"""
        query_input = TerminalUI._create_command_query()
        state = {
            "candidates": match_commands("/"),
            "selected": 0,
        }

        def selected_command() -> str | None:
            candidates = state["candidates"]
            if not candidates:
                return None
            return candidates[state["selected"]]

        def render_candidates() -> FormattedText:
            candidates = state["candidates"]
            if not candidates:
                return FormattedText([
                    ("class:command-empty", "  没有匹配的命令"),
                ])

            width = max(len(command) for command in COMMANDS)
            fragments: list[tuple[str, str]] = []
            for index, command in enumerate(candidates):
                selected = index == state["selected"]
                suffix = ".selected" if selected else ""
                marker = "›" if selected else " "
                fragments.extend([
                    (f"class:command-item{suffix}", f" {marker} {command:<{width}}  "),
                    (
                        f"class:command-description{suffix}",
                        f"{COMMANDS[command]} ",
                    ),
                    ("", "\n"),
                ])
            return FormattedText(fragments)

        candidate_control = FormattedTextControl(render_candidates)
        candidate_window = Window(
            content=candidate_control,
            height=Dimension.exact(len(COMMANDS)),
            always_hide_cursor=True,
        )

        def refresh_candidates(_buffer) -> None:
            previous = selected_command()
            candidates = match_commands(query_input.text)
            state["candidates"] = candidates
            if previous in candidates:
                state["selected"] = candidates.index(previous)
            else:
                state["selected"] = 0
            get_app().invalidate()

        query_input.buffer.on_text_changed += refresh_candidates
        bindings = KeyBindings()

        @bindings.add("up", eager=True)
        def _select_previous(event) -> None:
            candidates = state["candidates"]
            if candidates:
                state["selected"] = (state["selected"] - 1) % len(candidates)
                event.app.invalidate()

        @bindings.add("down", eager=True)
        def _select_next(event) -> None:
            candidates = state["candidates"]
            if candidates:
                state["selected"] = (state["selected"] + 1) % len(candidates)
                event.app.invalidate()

        @bindings.add("enter", eager=True)
        def _accept(event) -> None:
            command = selected_command()
            if command is not None:
                event.app.exit(result=command)

        @bindings.add("escape", eager=True)
        def _cancel(event) -> None:
            event.app.exit(result=None)

        @bindings.add("backspace", eager=True)
        def _backspace(event) -> None:
            # 只剩开头的 / 时再次退格，相当于关闭命令面板。
            if query_input.text == "/":
                event.app.exit(result=None)
            else:
                query_input.buffer.delete_before_cursor()

        dialog = Dialog(
            title="可用命令",
            body=HSplit([
                Label(text="继续输入以筛选，↑/↓ 选择，Enter 确认，Esc 取消。"),
                Frame(query_input, title="命令搜索"),
                candidate_window,
            ], padding=1),
            buttons=[],
            with_background=True,
        )
        app: Application[str | None] = Application(
            layout=Layout(dialog, focused_element=query_input),
            key_bindings=merge_key_bindings([load_key_bindings(), bindings]),
            mouse_support=True,
            style=CLI_STYLE,
            full_screen=True,
        )
        return TerminalUI._run_full_screen(app)

    @staticmethod
    def _run_conversation_dialog(
        *, title: str, values: list[tuple[str, str]], default: str
    ) -> str | None:
        """显示会话列表，并让 Enter 真正完成选择。

        prompt-toolkit 自带的 radiolist_dialog 需要先选中，再 Tab 到“确定”按钮。
        这里使用同一个 RadioList 控件自行组装窗口，让方向键 + Enter 的行为
        更接近日常命令面板，也减少一次不直观的键盘操作。
        """
        radio_list = RadioList(values=values, default=default)
        bindings = KeyBindings()

        @bindings.add("enter", eager=True)
        def _accept(event) -> None:
            event.app.exit(result=radio_list.current_value)

        @bindings.add("escape", eager=True)
        def _cancel(event) -> None:
            event.app.exit(result=None)

        dialog = Dialog(
            title=title,
            body=HSplit([
                Label(text="使用 ↑/↓ 选择，Enter 确认，Esc 取消。"),
                radio_list,
            ], padding=1),
            buttons=[],
            with_background=True,
        )
        app: Application[str | None] = Application(
            layout=Layout(dialog, focused_element=radio_list),
            key_bindings=merge_key_bindings([load_key_bindings(), bindings]),
            mouse_support=True,
            style=CLI_STYLE,
            full_screen=True,
        )
        return TerminalUI._run_full_screen(app)

    @staticmethod
    def _run_full_screen(app: Application):
        """运行临时全屏窗口，并在退出后清除 Windows 终端中的残留画面。

        某些终端恢复备用屏幕时会重新显示窗口打开前的内容，看起来像会话列表
        被加载了两次。把清理放在 ``finally`` 中，可以同时覆盖确认、取消和异常退出。
        """
        try:
            return app.run()
        finally:
            clear()

    def confirm_delete(self, title: str) -> bool:
        dialog = yes_no_dialog(
            title="确认删除",
            text=f"确定删除会话“{title}”吗？此操作无法撤销。",
            yes_text="删除",
            no_text="取消",
            style=CLI_STYLE,
        )
        return self._run_dialog(dialog, escape_result=False)

    @staticmethod
    def _run_dialog(dialog, escape_result):
        """给 prompt-toolkit 内置窗口补上统一的 Esc 取消行为。"""
        escape_bindings = KeyBindings()

        @escape_bindings.add("escape", eager=True)
        def _cancel(event) -> None:
            event.app.exit(result=escape_result)

        dialog.key_bindings = merge_key_bindings([
            dialog.key_bindings,
            escape_bindings,
        ])
        return dialog.run()

    @staticmethod
    def help_text() -> str:
        lines = ["\n可用命令："]
        width = max(len(command) for command in COMMANDS)
        for command, description in COMMANDS.items():
            suffix = " <名称>" if command == "/title" else ""
            lines.append(f"  {command:<{width}}{suffix:<8} {description}")
        lines.extend([
            "",
            "快捷键：",
            "  Ctrl+N       新建对话",
            "  Ctrl+L       清空屏幕",
            "  Alt+Enter    输入多行内容",
            "  ↑/↓          搜索历史输入",
            "  Ctrl+C       取消当前输入并退出",
            "",
        ])
        return "\n".join(lines)
