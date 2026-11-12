"""AI 侦探助手：只基于公开信息 + 玩家笔记给推理建议。

设计边界（与参考项目 Scottish 的 AI 搭档类似）：
- 绝不传入 murderer / truth / 私密线索；
- 只做“可疑点/下一步盘问建议”，不替玩家下结论；
- 调用失败时返回可解释的兜底文案，不影响主流程。
"""
from __future__ import annotations

from nodes import _safe_llm_text
from prompts import _untrusted


def build_detective_assistant_prompt(
    *,
    suspects: list[str],
    public_clues: list[str],
    relations: list[str],
    notes: str,
    round_num: int,
) -> str:
    """构建侦探助手 prompt。输入必须由调用方白名单筛选：只能有公开信息。"""
    suspects = suspects or []
    public_clues = public_clues or []
    relations = relations or []
    notes = str(notes or "")
    suspect_text = "、".join(_untrusted(str(s)) for s in suspects) if suspects else "（无）"
    clue_text = "\n".join(f"- {_untrusted(str(c))}" for c in public_clues) if public_clues else "（暂无公开线索）"
    relation_text = "\n".join(f"- {_untrusted(str(r))}" for r in relations) if relations else "（暂无公开关系）"
    notes_text = _untrusted(notes.strip()[:2000]) if notes.strip() else "（玩家还没有记笔记）"
    return f"""你是一名剧本杀侦探助手，只基于下面这些【公开信息】和【玩家自己的笔记】做推理辅助。

【嫌疑人】{suspect_text}
【当前轮次】第 {round_num} 轮
【公开线索】
{clue_text}
【公开人物关系】
{relation_text}
【玩家笔记】
{notes_text}

请输出 3–5 条短建议，每条一行，格式：
- 可疑点：……
- 建议盘问：……
- 可核对：……

要求：
1. 不要编造未提供的线索、动机或真相；
2. 不要直接断言“某某就是凶手”，只能给可疑点和可验证的下一步；
3. 不要提及任何“私密线索”“真相”“凶手是谁”等系统内部信息；
4. 语言简洁，中文，适合直接显示在侧边栏。
"""


def analyze_case(
    *,
    suspects: list[str],
    public_clues: list[str],
    relations: list[str],
    notes: str,
    round_num: int,
) -> str:
    """调用 LLM 生成侦探建议；失败时返回可读的兜底文案。"""
    prompt = build_detective_assistant_prompt(
        suspects=suspects,
        public_clues=public_clues,
        relations=relations,
        notes=notes,
        round_num=round_num,
    )
    return _safe_llm_text(
        prompt,
        purpose="assistant",
        fallback="（助手暂时没有新的分析，建议先核对公开线索和人物关系。）",
    )
