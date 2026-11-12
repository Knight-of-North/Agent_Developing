"""
确定性校验 / 规范化 / 兜底工具（从 nodes.py 拆出）。

这些都是"不调 LLM 的纯逻辑"，负责把 LLM 的输出校验、纠正、抢救。
分层思想：LLM 负责生成，这些函数负责"兜底"——
不指望 LLM 100% 听话，而是准备好它不听话时的退路。
"""
import json
import re

# forbidden 词的停用词表：LLM 偶尔把代词/虚词当成"违禁词"（最坏情况是「我」）。
# 一旦命中，ai_player_turn_node 会把**每一句正常台词**都判定成泄密，3 次重试
# 耗尽后整局 AI 发言都退化成固定兜底句。这些词对"泄密检测"没有任何区分度，
# 必须在入库前剔掉（R3 只处理了类型，没处理语义）。
_FORBIDDEN_STOPWORDS = frozenset({
    "我", "你", "他", "她", "它", "我们", "你们", "他们", "她们",
    "的", "了", "是", "在", "不", "有", "和", "就", "都", "也", "很",
    "说", "人", "这", "那", "一个", "什么", "怎么",
})

# 每个角色最多保留的违禁词数量（prompt 里要求 2~4 个，超出的截断）
MAX_FORBIDDEN_WORDS = 4


def _coerce_text(value) -> str:
    """把 LLM 输出的"本该是字符串"的字段安全转成 str。

    R3 只给 _extract_speak 补了类型防御；secret / personal_script 等字段仍是裸的——
    LLM 返回 `"secret": 123` 时 _normalize_secret_first_person 的 .startswith
    直接抛 AttributeError，绕过 F1「异常不出节点」的兜底，整局剧本生成失败。
    数字转成字符串（信息不丢），其余容器类型一律当缺失（交给自洽校验打回重试）。
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _sanitize_forbidden(forbidden, name: str = "", suspect_names: list[str] | None = None) -> list[str]:
    """清洗 forbidden 词表：剔停用词 / 剔角色名 / 去重 / 截断。

    为什么必须做：forbidden 是 AI 发言的"泄密检测器"，检测器本身必须比对
    秘密更有区分度。若表里混进「我」这类高频词，正常发言也会被判泄露；
    若混进别的角色名（"张三"），AI 每次称呼对方都会触发误报。
    """
    if not isinstance(forbidden, list):
        return []
    others = {n for n in (suspect_names or []) if n and n != name}
    cleaned: list[str] = []
    for word in forbidden:
        w = _coerce_text(word).strip()
        if len(w) < 2 or w in _FORBIDDEN_STOPWORDS or w in others or w == name:
            continue
        if w not in cleaned:
            cleaned.append(w)
        if len(cleaned) >= MAX_FORBIDDEN_WORDS:
            break
    return cleaned


def _parse_json(text: str) -> dict:
    """从模型输出里安全提取 JSON。

    LLM 输出不规整是常态，逐级放宽尝试：
    1. 直接 json.loads（最理想）
    2. 提取 ```json ... ``` 或 ``` ``` 代码块里的内容
    3. 从第一个 { 截取到最后一个 }（LLM 常在 JSON 前后加"好的，这是剧本："等引导文字）
    都失败才返回 {"raw": 原文}，交给下游兜底。
    """
    text = text.strip()

    # 先去掉首尾的 ``` 围栏（无论有没有 json 语言标记）
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 兜底 1：从文本里提取 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 兜底 2：从第一个 { 截取到最后一个 }（忽略 JSON 前后的引导文字）
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return {"raw": text}


def _enforce_names(script: dict, names: list[str]) -> dict:
    """兜底：强制剧本里的嫌疑人名字 = 我们抽的名字。

    名字是后续 choose_role / route_speaker / distribute_clues 的"主键"，
    LLM 可能不听话改了名字，必须改回来，否则选角色、轮流发言全乱。
    策略：尽量保留 LLM 给每个位置编的 secret / forbidden，只替换 name。

    同时清洗 forbidden（_sanitize_forbidden）——它是发言泄密检测器的词表，
    混进代词/角色名会让每一句正常台词都被判泄露。
    """
    suspects = script.get("suspects") or []
    if not isinstance(suspects, list):
        suspects = []
    fixed = []
    for i, name in enumerate(names):
        if i < len(suspects) and isinstance(suspects[i], dict):
            s = dict(suspects[i])
            s["name"] = name           # 只改名字，保留它编的 secret / forbidden
        else:
            # LLM 少给了嫌疑人，补一个空壳
            s = {"name": name, "secret": "待补充", "forbidden": []}
        s["forbidden"] = _sanitize_forbidden(s.get("forbidden"), name, names)
        fixed.append(s)
    script["suspects"] = fixed
    return script


def _normalize_secret_first_person(secret: str) -> str:
    """兜底：把 secret 开头的第三人称代词替换为第一人称。

    LLM 偶尔偷懒用「他/她」写自己的秘密（应该是第一人称「我」），破坏代入感。
    只替换 secret 开头的代词（其余地方的代词可能指别人，不动）。
    非字符串输入（LLM 返回数字/对象）先经 _coerce_text 归一，避免
    `.startswith` 抛 AttributeError 炸穿整局生成（R3 同类问题）。
    """
    secret = _coerce_text(secret)
    if not secret:
        return secret
    # 按长度从长到短匹配，避免「她的」被先匹配成「她」+「的」
    for old, new in [("她的", "我的"), ("他的", "我的"), ("她", "我"), ("他", "我")]:
        if secret.startswith(old):
            return new + secret[len(old):]
    return secret


def _normalize_secrets(script: dict) -> dict:
    """把所有 suspect 的 secret 规范化为第一人称开头（非字符串先归一）。"""
    for s in script.get("suspects") or []:
        if isinstance(s, dict) and "secret" in s:
            s["secret"] = _normalize_secret_first_person(s["secret"])
    return script


def _fallback_clues(script: dict) -> dict:
    """兜底：LLM 没按新格式（public_clues/private_clues）输出时，从旧 clues 字段抢救。

    这是"确定性兜底"思想：不指望 LLM 100% 听话，而是准备好它不听话时的退路。
    旧格式的 clues 是没标注持有者的纯字符串列表，这里把它切分：
    前 2 条当公共线索，剩下的轮转分配给各嫌疑人，保证信息差仍然成立。
    """
    if script.get("public_clues") or script.get("private_clues"):
        return script   # 已经是新格式，不用动

    suspects = script.get("suspects") or []
    names = [s.get("name") for s in suspects if s.get("name")]
    old_clues = script.get("clues") or []
    if not old_clues:
        return script   # 连旧线索都没有，放弃兜底

    # 前 2 条公开，剩余轮转分配（第 i 条分给第 i % len(names) 个嫌疑人）
    public = old_clues[:2]
    private = []
    if names:
        for i, c in enumerate(old_clues[2:]):
            private.append({"holder": names[i % len(names)], "content": c})
    else:
        public = old_clues   # 没有嫌疑人名单，全部当公共线索

    script["public_clues"] = public
    script["private_clues"] = private
    return script


def _filter_relations(script: dict) -> dict:
    """兜底：清洗 relations 里引用名单外角色的关系边。

    LLM 生成剧本时，可能把 background_story 素材种子里的背景人物
    （老师/管理员/路人等，不在嫌疑人名单内）写进 relations——
    这些名字会污染提示词里的人物关系上下文，也可能让后续页面按名单索引时失配。
    这里确定性剔除：关系边两端必须都在「嫌疑人名单 ∪ {死者}」内。

    注意：这里做的是"整条边剔除"，而不是"把名单外名字改成某人"——
    名单外角色不是登场角色，他/她的关系对玩家没有盘问价值，留着反而误导。
    """
    suspects = script.get("suspects") or []
    names = [s.get("name", "") for s in suspects if isinstance(s, dict) and s.get("name")]
    valid = set(names) | {"死者"}

    rels = script.get("relations") or []
    kept = [
        r for r in rels
        if isinstance(r, dict)
        and r.get("from") in valid and r.get("to") in valid
    ]
    if len(kept) != len(rels):
        script["relations"] = kept
    return script


def _ensure_clues(script: dict) -> dict:
    """最后防线：剧本完全没有线索时，从嫌疑人的 secret 生成兜底线索。

    LLM 偶尔整份剧本都不生成 public_clues / private_clues（空列表），
    自洽校验会报"没有任何线索"，玩家一局白板玩不下去。
    兜底策略（纯确定性，不调 LLM）：把每个嫌疑人的 secret 作为一条
    "只有他自己知道"的私密线索，holder 就是本人——每个角色至少 1 条，
    信息差仍然成立（别人的秘密你不知道）。secret 缺失的用占位文本，
    保证数量对得上。

    这层是"最后防线"：正常剧本（LLM 有线索）完全不受影响。
    """
    if script.get("private_clues") or script.get("public_clues"):
        return script   # 已有线索，不动

    suspects = script.get("suspects") or []
    private = []
    for s in suspects:
        if not isinstance(s, dict):
            continue
        name = s.get("name", "")
        secret = s.get("secret", "").strip()
        if not name:
            continue
        if secret:
            content = f"我自己的秘密：{secret}"
        else:
            content = "我知道一些关于自己的事，但一时想不起来。"
        private.append({"holder": name, "content": content})

    script["private_clues"] = private
    return script


def _extract_speak(out: dict) -> str:
    """从 LLM 解析结果里安全提取"公开发言"。

    JSON 解析成功时直接取 speak；解析失败时 _parse_json 返回 {"raw": 全文}，
    此时绝不能用 raw（raw 里混着 think 内心戏，公开出去 = 凶手剧透）。

    用 json.JSONDecoder().raw_decode() 从 raw 中扫描第一个合法 JSON 对象——
    它天然处理转义引号（\\"）、Unicode 转义（\\uXXXX）等情况，比正则健壮。
    扫描不到再退回正则兜底。

    R3：speak 必须是字符串——json.loads / raw_decode 解析出的 speak 可以是
    任意 JSON 值（dict/list/None/数字），原样返回会在下游 speak.strip() /
    `词 in speak` 处抛 TypeError 炸穿节点（绕过 F1"异常不出节点"兜底）。
    非字符串一律返回空串，走上层"解析失败重试"通道。
    """
    def _coerce(v) -> str:
        return v if isinstance(v, str) else ""

    if "speak" in out:
        return _coerce(out["speak"])
    raw = out.get("raw", "")

    # 用 JSONDecoder 从 raw 中扫描第一个合法 JSON 对象（天然处理转义）
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(raw):
        start = raw.find("{", idx)
        if start == -1:
            break
        try:
            obj, end = decoder.raw_decode(raw, start)
            if isinstance(obj, dict) and "speak" in obj:
                speak = _coerce(obj["speak"])
                if speak:
                    return speak
                idx = end   # speak 字段非字符串，继续向后扫描
                continue
            idx = end
        except json.JSONDecodeError:
            idx = start + 1   # 这个 { 不是合法 JSON 起点，往后找

    # 兜底：正则（处理 speak 值不含转义引号的极端格式）
    m = re.search(r'"speak"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    # L1：纯省略号，不夹带"（这个角色欲言又止）"这类破坏沉浸的元叙述
    return m.group(1) if m else "……"
