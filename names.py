"""
嫌疑人名字相关：名字池 + 抽样逻辑（Phase 5.5）。

从 nodes.py 拆出（拆分 God module 的第 1 部分）：
nodes.py 原本 700+ 行混了名字池数据、prompt、解析、节点，这里把"名字"这一
职责独立出来，让每个模块各司其职。
"""
import re
import random


# ---- 嫌疑人名字池（按背景风格分组）----
# 每组 16 个名字（男女混合），random.sample 从里面无放回抽样，
# 保证几乎每局的名字都不一样。
_NAME_POOLS = {
    "民国豪门": [
        "陆明轩", "顾则安", "沈长卿", "裴静山", "霍世昌", "宋怀远", "傅敬亭", "周既明",
        "沈碧如", "陆曼宁", "白素秋", "苏晚棠", "秦婉清", "顾念慈", "温若梅", "林淑仪",
    ],
    "校园怪谈": [
        "顾一舟", "林小北", "江叙", "许晏", "程野", "宋知夏", "周既白", "裴然",
        "林小满", "苏晚晴", "阮清", "叶听澜", "池念初", "温南乔", "纪云舒", "白露晞",
    ],
    "古风仙侠": [
        "萧暮云", "洛青崖", "沈星野", "慕寒", "顾长风", "谢流云", "楚怀瑾", "陆离",
        "洛清欢", "云知意", "苏挽月", "姜晚吟", "阮清歌", "白若溪", "温如故", "秦望舒",
    ],
    "现代都市": [
        "程亦辰", "陆则言", "沈默", "顾景行", "江叙白", "周叙", "许奕", "裴照",
        "苏念", "林晚意", "叶知秋", "池雨", "温以宁", "纪南乔", "白筱", "秦悦",
    ],
    "科幻末世": [
        "陆沉舟", "沈烬", "顾寒", "江澜", "宋曜", "周烬", "裴夜", "韩泽",
        "苏曜", "林烬", "叶澜", "池寒", "温澜", "纪星", "白月", "秦霜",
    ],
}

# "自由发挥"（以及任何没匹配上的背景）用所有池合并去重，名字风格最杂、随机性最大。
# dict.fromkeys 保序去重：O(k)，替代之前 `if x not in list` 的 O(k²) 线性扫描。
_MIXED_POOL = list(dict.fromkeys(n for pool in _NAME_POOLS.values() for n in pool))

# 本会话已用过的名字集合：避免重玩撞脸。
# 名字池每风格仅 16 个、混合池约 80 个，同一风格玩 4~5 局后重复概率显著上升，
# 撞名会瞬间唤起上一局的记忆、破坏"新故事"的感觉。抽样时优先排除已用名字。
_used_names: set[str] = set()


def _parse_names(text: str) -> list[str]:
    """解析用户输入的自定义名字列表（支持逗号/顿号/分号/冒号/竖线/斜杠/空格/换行分隔，去重保序）。

    注意：分隔符要同时覆盖中英文标点。之前只写了英文分号 `;`，
    用户打中文分号 `；`（U+FF1B）时匹配不上，导致"张三；李四"被当成一个名字，
    名字总数不足 3 个就静默退回随机——这正是"自定义名字没生效"的根因。
    """
    if not text:
        return []
    # 分隔符：中英文逗号 顿号 中英文分号 中英文冒号 竖线 斜杠 以及所有空白（空格/tab/换行）
    text = re.sub(r"[，,、;；:：|/\s]+", " ", text.strip())
    seen = set()
    result = []
    for name in text.split():
        name = name.strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _pick_suspect_names(background: str, custom_names: list[str] | None = None) -> list[str]:
    """选嫌疑人名字：用户自定义优先，否则按背景风格随机抽（4~6 个，不重复）。

    为什么随机抽不用 LLM？LLM 的"随机"趋同（翻来覆去那几个高频名），
    random.sample 无放回抽样组合数巨大。但用户可能想用自己的朋友/同学名
    代入角色——这时自定义优先，随机兜底。
    """
    # 用户自定义名字：至少 3 个才采用，否则退回随机（名字太少撑不起剧本杀）
    if custom_names:
        cleaned = [n.strip() for n in custom_names if n and n.strip()]
        if len(cleaned) >= 3:
            return cleaned[:6]   # 最多 6 个嫌疑人

    pool = _NAME_POOLS.get(background, _MIXED_POOL)
    # 优先从未用过的名字里抽，避免和之前几局撞脸
    fresh = [n for n in pool if n not in _used_names]
    if len(fresh) < 4:
        # 新鲜名字不够抽一整局了，重置（允许从头复用）
        _used_names.clear()
        fresh = list(pool)
    n = random.randint(4, 6)          # 嫌疑人数量也随机，增强可玩性
    n = min(n, len(fresh))            # 名字池不够抽时退而求其次
    picked = random.sample(fresh, n)
    _used_names.update(picked)        # 标记本会话已用
    return picked
