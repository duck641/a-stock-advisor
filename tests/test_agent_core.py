import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import setup_wizard
from config import LLMConfig
from tools import TOOLS
from tools.core.skill_manager import load_skill_ref


class AgentCoreTests(unittest.TestCase):
    def test_deepseek_defaults_are_current(self):
        config = LLMConfig(_env_file=None, provider="deepseek", api_key="test")
        self.assertEqual(config.model, "deepseek-flash")
        self.assertEqual(config.context_window, 1_000_000)
        self.assertEqual(config.temperature, 0.2)

    def test_skill_reference_tool_is_bound_and_confined(self):
        self.assertIn("load_skill_ref", {tool.name for tool in TOOLS})
        result = load_skill_ref.invoke({
            "skill_name": "stock-analysis-team",
            "file_path": "references/analysis-framework.md",
        })
        self.assertTrue(result.startswith("[加载关联文件]"))

        blocked = load_skill_ref.invoke({
            "skill_name": "stock-analysis-team",
            "file_path": "../../config.py",
        })
        self.assertTrue(blocked.startswith("不允许访问"))

    def test_custom_provider_setup_writes_required_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            answers = iter([
                "7", "custom-model", "secret", "https://example.test/v1",
                "32000", "Y", "N",
            ])
            with (
                patch.object(setup_wizard, "_env_path", return_value=env_path),
                patch("builtins.input", side_effect=lambda _prompt: next(answers)),
                patch("sys.stdout", new=io.StringIO()),
            ):
                setup_wizard.run_setup()

            content = env_path.read_text(encoding="utf-8")
            self.assertIn("LLM_PROVIDER=custom", content)
            self.assertIn("LLM_MODEL=custom-model", content)
            self.assertIn("LLM_BASE_URL=https://example.test/v1", content)
            self.assertIn("LLM_CONTEXT_WINDOW=32000", content)


if __name__ == "__main__":
    unittest.main()
