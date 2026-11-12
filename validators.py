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

# 名单外人物名检测：中文常见姓氏 + 1~3 个汉字（2~4 字姓名）。
# 只用于「自由文本里出现的人名」预警/清理，不用于替换剧本名。
_OUTSIDER_NAME_RE = re.compile(
    r"(?:^|[，。！？、；：\s“”「」『』（）()]|被|和|与|跟|向|对|让|叫|是|把|由|给|从|到|在|说|问|找|见|看|听|有|没|发现|看到|看见|遇见|认识|喜欢|讨厌|恨|爱|怕|怀疑|指控|告诉|质疑|提到)"
    r"([赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫房裘解应宗丁宣邓郁单杭洪包诸左石崔吉龚程邢裴陆荣翁荀羊惠甄曲封芮羿储靳段富车侯全班秋仲伊宫宁仇栾甘厉戎祖武符刘景詹束龙叶黎薄印宿白怀蒲从鄂索咸赖卓蔺屠蒙池乔闻党翟谭贡劳姬申扶冉郏浦尚农温别庄晏柴瞿阎慕连茹习艾鱼容向古易慎戈廖庾居衡步都耿满弘匡国文寇广禄东欧沃利蔚越隆师巩聂晁勾融冷辛简饶曾沙鞠丰巢关查后荆红游竺权盖益]"
    r"[一-龥]{1,3}?"
    r"(?=$|[，。！？、；：\s“”「」『』（）()]|的|是|在|说|问|答|道|喊|叫|笑|哭|看|见|听|想|要|会|能|把|被|给|让|对|向|从|到|跟|和|与|及|而|也|还|又|就|正|曾|已|将|未|没|不|很|最|更|昨|今|明|当|那|这|他|她|它|我|你|们|杀|死|害|骗|瞒|藏|偷|打|骂|威胁|承认|发现|告诉|质问|指认|离开|回到|走进|站在|拿着|看到|听到|来|去|走|坐|站|回|出|进|因为|所以|但是|然后|之后|之前|时|后|前|中))"
)

# 角色/职业后缀："刘老师""张医生""周管理员"不是独立角色名，保留原称谓。
_ROLE_SUFFIXES = (
    "老师", "医生", "警察", "保安", "管理员", "辅导员", "院长", "主任", "老板",
    "同学", "朋友", "父亲", "母亲", "哥哥", "姐姐", "弟弟", "妹妹", "叔叔", "阿姨",
    "先生", "小姐", "女士",
)

# 高频非人名词/称谓，避免姓名启发式把普通词当成人名。
_NON_PERSON_WORDS = frozenset({
    "自己", "大家", "众人", "有人", "某人", "主持人", "嫌疑人", "凶手", "死者",
    "警方", "警察", "老师", "医生", "学生", "监控", "录像", "手机", "钥匙", "纸条",
    "证据", "线索", "时间", "现场", "房间", "食堂", "图书馆", "实验室", "办公室",
    "校门口", "保安", "管理员", "辅导员", "院长", "主任", "老板", "同学", "朋友",
    "家人", "父亲", "母亲", "哥哥", "姐姐", "弟弟", "妹妹", "叔叔", "阿姨", "先生",
    "小姐", "女士", "男人", "女人", "晚上", "白天", "凌晨", "傍晚", "今天", "昨天",
    "明天", "早上", "中午", "下午", "周末", "春节", "中秋", "国庆",
})


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


def _extract_outsider_names(text: str, roster: set[str]) -> list[str]:
    """从一段文本里提取名单外人物名（按出现顺序去重）。"""
    found: list[str] = []
    if not isinstance(text, str) or not text:
        return found
    for m in _OUTSIDER_NAME_RE.finditer(text):
        name = m.group(1)
        if name in roster or name in found:
            continue
        # “周一早上/周五晚上”是时间，不是人物名
        if name.startswith(("周一", "周二", "周三", "周四", "周五", "周六", "周日", "周天")):
            continue
        # “张家界/李家庄”这类地名不是人物名；单字后缀只拦 3 字以上，
        # 避免误伤“张路”这种两字人名。
        if len(name) >= 3 and name.endswith(("界", "省", "市", "县", "区", "镇", "村", "街", "路", "楼")):
            continue
        if name.endswith(("公园", "广场", "学校", "大学", "公司", "车站", "机场", "酒店",
                          "餐厅", "食堂", "图书馆", "实验室", "办公室", "宿舍", "门口")):
            continue
        # 名单内名字的子串（如「舒飞」出现在「舒飞扬」里）不算 outsider
        if any(name in r or r in name for r in roster if r):
            continue
        if name in _NON_PERSON_WORDS:
            continue
        if any(name.endswith(suffix) for suffix in _ROLE_SUFFIXES):
            continue
        found.append(name)
    return found


def _find_outsider_names(script: dict) -> list[str]:
    """找出剧本自由文本里出现的「名单外人物名」。

    背景：_enforce_names 只把 suspects[i].name 强制改成抽到的名字，但 LLM 在
    truth / personal_script / 线索内容这些自由文本里仍可能写入自己编的人名
    （例如玩家只设定了 4 个角色，DM 揭晓里却出现「张浩」）。这些名字不在
    suspects 名单里，前端无法索引、玩家也无法盘问，属于必须修掉的数据污染。
    """
    suspects = script.get("suspects") or []
    roster = {s.get("name", "") for s in suspects if isinstance(s, dict) and s.get("name")}
    roster.add("死者")

    texts = [script.get("background", ""), script.get("truth", "")]
    for s in suspects:
        if isinstance(s, dict):
            texts.extend([s.get("secret", ""), s.get("personal_script", ""), s.get("alibi", ""), s.get("relation_to_victim", "")])
    for c in script.get("public_clues") or []:
        texts.append(c if isinstance(c, str) else "")
    for c in script.get("hidden_clues") or []:
        texts.append(c if isinstance(c, str) else "")
    for c in script.get("private_clues") or []:
        if isinstance(c, dict):
            texts.append(c.get("content", ""))

    found: list[str] = []
    for text in texts:
        for name in _extract_outsider_names(text, roster):
            if name not in found:
                found.append(name)
    return found


def _replace_outsider_names(text: str, roster: set[str]) -> str:
    """把一段文本里的名单外人物名替换为「某人」，并返回替换后的文本。"""
    if not isinstance(text, str) or not text:
        return text
    for name in _extract_outsider_names(text, roster):
        text = text.replace(name, "某人")
    return text


def _sanitize_outsider_names(script: dict) -> dict:
    """把剧本自由文本中的名单外人物名统一替换为「某人」。

    这是展示层前的最后一道防线；配合 prompt 的名单铁律和自洽校验，
    避免 LLM 编出来的「张浩」之类角色出现在 DM 揭晓/个人剧本里。
    """
    suspects = script.get("suspects") or []
    roster = {s.get("name", "") for s in suspects if isinstance(s, dict) and s.get("name")}
    roster.add("死者")

    for key in ("background", "truth"):
        script[key] = _replace_outsider_names(script.get(key, ""), roster)
    for s in suspects:
        if not isinstance(s, dict):
            continue
        for key in ("secret", "personal_script", "alibi", "relation_to_victim", "profession", "task"):
            if key in s:
                s[key] = _replace_outsider_names(s.get(key, ""), roster)
    for key in ("public_clues", "hidden_clues"):
        script[key] = [_replace_outsider_names(c, roster) if isinstance(c, str) else c
                       for c in (script.get(key) or [])]
    for c in script.get("private_clues") or []:
        if isinstance(c, dict):
            c["content"] = _replace_outsider_names(c.get("content", ""), roster)
    for r in script.get("relations") or []:
        if isinstance(r, dict) and "rel" in r:
            r["rel"] = _replace_outsider_names(r.get("rel", ""), roster)
    return script


def _sanitize_text_for_script(text: str, script: dict) -> str:
    """清洗单段 LLM 输出里出现的名单外人物名（DM 台词 / AI 公开发言用）。"""
    suspects = script.get("suspects") or []
    roster = {s.get("name", "") for s in suspects if isinstance(s, dict) and s.get("name")}
    roster.add("死者")
    return _replace_outsider_names(text, roster)


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
    if isinstance(old_clues, str):
        old_clues = [old_clues]
    if not isinstance(old_clues, list):
        old_clues = []
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
