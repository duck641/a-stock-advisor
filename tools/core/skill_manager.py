"""
技能管理工具 - 渐进式披露

加载流程：
  1. load_skill(name)       → 返回 SKILL.md 完整内容 + linked_files 目录列表
  2. load_skill_ref(name, ref)  → 返回具体某个 linked file 的内容（按需加载）
"""

from langchain_core.tools import tool
from pathlib import Path
from typing import Optional


SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"


def _find_skill(name: str) -> Optional[Path]:
    """查找 skill 目录"""
    p = SKILLS_DIR / name
    return p if p.is_dir() and (p / "SKILL.md").exists() else None


@tool
def load_skill(skill_name: str) -> str:
    """
    加载技能的主文件 SKILL.md 内容，同时列出所有关联的 linked files。
    
    关联文件按需加载：如需查看某个 linked file 的具体内容，调用 load_skill_ref()。
    
    参数:
        skill_name: 技能名称，通过 list_skills() 查看可用技能

    返回:
        SKILL.md 内容 + linked_files 目录（含文件名和一句话描述）
    """
    skill_dir = _find_skill(skill_name)
    if not skill_dir:
        return f"未找到技能 '{skill_name}'"

    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

    # 收集 linked files，只列出文件名，不读内容
    refs_dir = skill_dir / "references"
    scripts_dir = skill_dir / "scripts"
    linked = []
    
    if refs_dir.exists():
        for f in sorted(refs_dir.iterdir()):
            if f.is_file():
                linked.append(("references", f.name))
    if scripts_dir.exists():
        for f in sorted(scripts_dir.iterdir()):
            if f.is_file():
                linked.append(("scripts", f.name))

    result = f"[系统] 已加载技能「{skill_name}」\n\n{content}"

    if linked:
        parts = ["\n\n---\n关联文件（按需加载，调用 load_skill_ref 查看具体内容）:"]
        for cat, name in linked:
            parts.append(f"  [{cat}] {name}")
        result += "\n".join(parts)

    return result


@tool
def load_skill_ref(skill_name: str, file_path: str) -> str:
    """
    按需加载技能目录下的某个关联文件内容（references/ 或 scripts/ 下的文件）。

    参数:
        skill_name: 技能名称
        file_path: 文件路径，如 "references/analysis-framework.md" 或 "scripts/chart_generator.py"

    返回:
        文件内容
    """
    skill_dir = _find_skill(skill_name)
    if not skill_dir:
        return f"未找到技能 '{skill_name}'"

    full_path = skill_dir / file_path
    if not full_path.exists():
        return f"未找到文件 '{file_path}'"

    if not full_path.is_file():
        return f"路径 '{file_path}' 不是文件"

    # 限制只能访问 references/ 和 scripts/ 目录
    allowed_prefixes = [str(skill_dir / "references"), str(skill_dir / "scripts")]
    if not any(str(full_path).startswith(p) for p in allowed_prefixes):
        return f"不允许访问 '{file_path}'，只能访问 references/ 和 scripts/ 目录下的文件"

    content = full_path.read_text(encoding="utf-8")
    return f"[加载关联文件] {file_path}\n\n{content}"


@tool
def list_skills(category: Optional[str] = "") -> str:
    """
    列出所有可用的技能模块。

    参数:
        category: 分类筛选（可选），不传则列出全部

    返回:
        技能列表（含名称和描述）
    """
    if not SKILLS_DIR.exists():
        return "未找到 skills 目录"

    skills = sorted([d.name for d in SKILLS_DIR.iterdir() if d.is_dir() and (d / "SKILL.md").exists()])
    if not skills:
        return "暂无可用技能"

    lines = ["可用技能列表:"]
    lines.append("=" * 40)
    for s in skills:
        sk_path = SKILLS_DIR / s / "SKILL.md"
        desc = ""
        for line in sk_path.read_text(encoding="utf-8").split("\n"):
            if line.startswith("description:"):
                desc = line[len("description:"):].strip().strip('"').strip("'")
                break
        lines.append(f"  {s}")
        if desc:
            lines.append(f"    {desc[:120]}")
    lines.append("=" * 40)
    lines.append(f"  共 {len(skills)} 个技能")
    lines.append("提示: 使用 load_skill(<名称>) 加载技能详情")
    return "\n".join(lines)
