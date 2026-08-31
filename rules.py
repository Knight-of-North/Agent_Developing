"""
游戏规则引擎（纯函数，零 LLM、零状态依赖）—— M2 从 nodes.py 平移。

这里集中"可独立单测的确定性游戏规则"：
- clue_mentioned / update_revealed_clues ：线索公开判定（H3/H5）
- detect_addressed                      ：发言点名检测（H18）
- clue_topic_map                        ：线索方向标签映射（H13）
- count_accusations_against             ：指控计数启发式（M4）
- judge_ending                          ：玩家结局判定（含弃权分支 L8）

nodes.py 通过 `from rules import X as _X` 保持旧名转发，
外部 import 路径（nodes._clue_mentioned 等）不变，测试无需改动。
"""
from collections import Counter


def _ngrams(s: str, n: int = 2) -> set[str]:
    """相邻 n 元字符组集合（H5：保留局部语序）。"""
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def clue_mentioned(clue: str, speak: str, threshold: float = 0.5) -> bool:
    """判断线索是否在发言中被提及（H3：容忍转述/省略；H5：2-gram 降误报）。

    三级判定，从严到宽：
    1. 精确子串（原文引用/转述）——直接命中；
    2. 2-gram 重叠率：把线索切成相邻二字组，统计有多少组出现在发言中。
       相比单字重叠，"字碰巧出现过"的噪声按平方级下降——
       "书房里有一把带血的刀" vs "他把书房的菜刀擦干净了，没有血迹"
       这类同字异义不再误报；
    3. 极短线索（<3 个 bigram）只有精确子串一条路，杜绝小集合高命中。
    """
    if not clue:
        return False
    if clue in speak:
        return True
    clue_grams = _ngrams(clue)
    if len(clue_grams) < 3:
        return False
    hit = sum(1 for g in clue_grams if g in speak)
    return hit / len(clue_grams) >= threshold


def update_revealed_clues(speak: str, distributed_clues: dict, revealed_clues: dict) -> dict:
    """发言后更新线索公开状态。用 clue_mentioned 容忍转述、防止单词误报。"""
    newly = {}
    for holder, clues in distributed_clues.items():
        for clue in clues:
            if clue in revealed_clues:
                continue
            if clue_mentioned(clue, speak):
                newly[clue] = holder
    return newly


def detect_addressed(speak: str, names: list[str]) -> str:
    """检测发言里点名了哪个嫌疑人（H18：长名优先，避免前缀重叠误匹配）。

    按名字长度降序匹配：名单同时含"江叙"和"江叙白"时，"江叙白你怎么看"
    优先命中"江叙白"而非短名"江叙"。不用中文边界正则——"江叙白你"里
    "你"是正常称呼字，边界正则会误阻断。
    """
    for n in sorted(names, key=len, reverse=True):
        if n and n in speak:
            return n
    return ""


def clue_topic_map(script: dict) -> dict:
    """建 {线索内容: 方向标签} 映射，供 DM 中场引导用（H13）。"""
    topic_map = {}
    for c in script.get("private_clues", []) or []:
        if isinstance(c, dict) and c.get("content"):
            topic = (c.get("topic") or "").strip()
            topic_map[c["content"]] = topic
    return topic_map


def apply_followup_guard(text: str, targets: list[str],
                         prev_follow: int, prev_consecutive: int) -> tuple[str, int, int]:
    """文本发言的"点名→追问"守卫（M3：human_turn 的 str/dict 两分支共用）。

    F5 语义：只有上一轮 follow_up==0 时新点名才给追问，连续点名不续期——
    防玩家霸麦饿死其他 AI。此前 str 分支有此守卫、dict 分支没有（死分支藏雷，
    接入 action 协议即引爆）；提取成共用函数后该规则只有一份实现，
    第三种分支不可能再出现语义分歧。

    返回 (addressed 被点名者, follow_up 追问权, consecutive 连续交锋计数)。
    """
    addressed = detect_addressed(text, targets)
    if addressed and prev_follow == 0:
        return addressed, 1, prev_consecutive + 1
    return addressed, 0, 0


def count_accusations_against(messages: list[dict], name: str) -> int:
    """统计其他发言者对 name 的指控次数（M4：不依赖"指控"二字的启发式）。

    命中任一即计一次：
    1. 发言含"指控"（原有规则）；
    2. name 与"凶手/真凶/杀人犯/杀人凶手"共现，且含"就是/肯定是/一定是/才是/是你/是他"等指认词。
    """
    culprit_words = ("凶手", "真凶", "杀人犯", "杀人凶手")
    pointer_words = ("就是", "肯定是", "一定是", "才是", "是你", "是他", "是她", "我看是", "分明是")
    count = 0
    for m in messages:
        if m.get("speaker") == name:
            continue
        content = m.get("content") or ""
        if "指控" in content:
            count += 1
            continue
        if name in content and any(w in content for w in culprit_words) \
                and any(p in content for p in pointer_words):
            count += 1
    return count


def judge_ending(state: dict) -> tuple[str, str]:
    """根据玩家行为判定结局类型，返回 (结局标签, 演绎提示)。纯确定性判定。

    L8：玩家弃权（None/未投）单列"置身事外"结局——
    "压根没投"和"投错被误导"是两种叙事，不能都算"被蒙蔽"。
    """
    user_role = state.get("user_role", "你")
    user_is_murderer = state.get("user_is_murderer", False)
    votes = state.get("votes", {})
    user_vote = votes.get(user_role, "未投")
    vote_winner = str(state.get("vote_winner", ""))
    script = state.get("script", {})
    murderer = get_murderer(script, [s.get("name", "") for s in script.get("suspects", [])])

    if user_is_murderer:
        if vote_winner != user_role:
            return "完美犯罪", f"（真人玩家 {user_role} 是真凶却成功脱罪！请点出他/她是如何瞒天过海、误导全场的）"
        return "凶手伏法", f"（真人玩家 {user_role} 是真凶但被识破投出，请演绎他/她的伏法与不甘）"

    if user_vote in (None, "", "未投"):
        return "置身事外", f"（真人玩家 {user_role} 弃权未投票，全程冷眼旁观，请演绎他置身事外的疏离感）"

    if murderer and user_vote == murderer:
        speak_count = sum(1 for m in state.get("messages", []) if m.get("speaker") == user_role)
        investigated = bool(state.get("investigated_clues"))
        if speak_count >= 3 or investigated:
            return "侦探", f"（真人玩家 {user_role} 投对了真凶且积极主导了推理，请夸赞他/她的洞察力）"
        return "幸运旁观者", f"（真人玩家 {user_role} 投对了但全程低调，像是个运气不错的旁观者）"
    return "被蒙蔽", f"（真人玩家 {user_role} 投错了、被真凶误导，请演绎'真凶逍遥法外'的遗憾尾声，让玩家恍然大悟又懊恼）"


def get_murderer(script: dict, suspect_names: list[str]) -> str:
    """提取凶手名字：优先 murderer 字段，否则从 truth 里找最后出现的嫌疑人名字兜底。

    兜底取"最后出现"而非"第一个出现"——truth 叙事通常先描述涉案人物、
    在末尾才揭露真凶，第一个出现的往往是无辜者（F4）。

    R11：旧实现遍历的是名单顺序，得到的是"名单序靠后"而非"truth 文本中
    位置靠后"，注释与行为不符。现按 truth 中的实际位置取最靠后者；
    子串重叠名（"江叙" ⊂ "江叙白"）位置相同时长名优先。
    """
    murderer = script.get("murderer", "")
    if murderer and murderer in suspect_names:
        return murderer
    truth = script.get("truth", "")
    found = ""
    best_pos = -1
    # 长名优先：位置相同（子串重叠）时不让短名抢走匹配
    for n in sorted((n for n in suspect_names if n), key=len, reverse=True):
        pos = truth.find(n)
        if pos != -1 and pos > best_pos:
            best_pos = pos
            found = n
    return found


def tally_votes(votes: dict) -> tuple[Counter, list[str]]:
    """统计有效票（过滤弃权 None），返回 (计数器, 最高票名单)。供 tally_node 复用。"""
    valid = {k: v for k, v in votes.items() if v}
    counter = Counter(valid.values())
    if not counter:
        return counter, []
    top = counter.most_common()
    max_n = top[0][1]
    winners = [k for k, v in top if v == max_n]
    return counter, winners
