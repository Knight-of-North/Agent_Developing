"""
节点函数 —— LangGraph 里的"演员"

一个节点 = 一个普通 Python 函数，接收整个 state，返回 state 的"部分更新"（dict）。
LangGraph 会自动把返回的 dict 合并回共享状态里。

Phase 1 只有两个节点：
1. generate_script_node：调大模型，生成结构化剧本
2. dungeon_master_node ：调大模型，生成主持人（DM）台词
"""
import os
import json
import re
from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek

# 从 .env 读取密钥（密钥绝不写死在代码里）
load_dotenv()

# 全局模型实例：整个程序共用一个，避免反复创建浪费资源
llm = ChatDeepSeek(
    model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    temperature=0.8,       # 创作类任务，调高一点让剧情更有变化
    max_tokens=4096,       # 剧本 + 台词较长，给足输出空间
)


def _parse_json(text: str) -> dict:
    """从模型输出里安全地提取 JSON。

    模型偶尔会在 JSON 外面多包一层 ```json ... ``` 标记，
    直接 json.loads 会报错，所以先剥掉再解析。
    """
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)   # 去掉开头的 ```json
    text = re.sub(r"\s*```$", "", text)            # 去掉结尾的 ```
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 解析失败兜底：把原文塞进 raw 字段，保证程序不崩
        return {"raw": text}


def generate_script_node(state: dict) -> dict:
    """节点 1：根据主题生成结构化剧本，并把阶段推进到 intro"""
    theme = state.get("theme", "民国豪门恩怨")

    prompt = f"""你是一名资深剧本杀编剧。请围绕主题「{theme}」创作一个完整的剧本杀剧本。

严格输出 JSON 格式，不要输出任何 JSON 以外的文字。字段如下：
{{
  "background": "案件背景故事，约100字",
  "suspects": [
    {{"name": "嫌疑人名字", "secret": "这个人隐藏的秘密"}}
  ],
  "clues": ["线索1", "线索2", "线索3", "线索4", "线索5"],
  "truth": "案件真相：凶手是谁、动机、作案手法"
}}"""

    resp = llm.invoke(prompt)           # 调用大模型
    script = _parse_json(resp.content)  # 把返回的 JSON 文本解析成 dict
    return {"script": script, "current_phase": "intro"}


def dungeon_master_node(state: dict) -> dict:
    """节点 2：主持人（DM）生成主持台词，并推进阶段"""
    script = state.get("script", {})
    phase = state.get("current_phase", "intro")

    background = script.get("background", script.get("raw", ""))
    suspects = script.get("suspects", [])
    clues = script.get("clues", [])

    prompt = f"""你是一位剧本杀主持人（DM），负责控场。当前阶段是【{phase}】。

剧本信息：
- 案件背景：{background}
- 嫌疑人：{suspects}
- 线索：{clues}

请用主持人的口吻，生成这一段主持台词，依次包含：
1. 开场：介绍案件背景和登场角色
2. 公布可公开的线索
3. 引导玩家讨论并进入投票环节
（Phase 1 简化版：一次性说完开场、线索、投票引导）"""

    resp = llm.invoke(prompt)
    # messages 用了 operator.add 这个 reducer，所以这里是"追加"而不是"覆盖"
    return {"messages": [resp.content], "current_phase": "vote"}


if __name__ == "__main__":
    # 单文件自测：直接调用节点看看效果
    out = generate_script_node({"theme": "校园密室"})
    print(out)
