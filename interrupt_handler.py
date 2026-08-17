"""
interrupt 处理公共逻辑（从 app.py / main.py 抽出）。

app.py（Streamlit，声明式 + rerun）和 main.py（终端，命令式 while 循环）的
控制流根本不同，不能强行统一成同一个循环。这里只抽取真正可复用的"纯逻辑"：
- 安全提取 interrupt 信息（避免 result["__interrupt__"][0].value 链式硬取 KeyError）
- 校验选角色 / 投票输入（两处原来各写一遍的校验逻辑）

新增 interrupt 类型时，两处的 if/elif 分发仍需各自加分支，但"提取 + 校验"
这部分只用维护这一处。
"""


def get_interrupt(result: dict) -> dict:
    """从图执行结果里安全提取 interrupt 信息，没有则返回空 dict。

    之前两处都写 result["__interrupt__"][0].value["type"] 链式硬取，
    interrupt 结构一旦变化就直接 KeyError。这里用 .get / getattr 逐层防御。

    注意：LangGraph 的 __interrupt__ 列表元素是 Interrupt 对象（内容在 .value 属性），
    不是 dict；但测试里可能用 dict 模拟，所以两种结构都兼容。
    """
    interrupts = result.get("__interrupt__") or []
    if not interrupts:
        return {}
    first = interrupts[0]
    if isinstance(first, dict):
        value = first.get("value")
    else:
        value = getattr(first, "value", None)
    return value if isinstance(value, dict) else {}


def get_interrupt_type(result: dict) -> str:
    """提取 interrupt 类型（choose_role / human_turn / human_vote），无则返回空串。"""
    return get_interrupt(result).get("type", "")


def validate_role_choice(chosen: str, names: list[str]) -> str:
    """校验选角色：合法则返回原值，非法则兜底到名单第一个。"""
    return chosen if chosen in names else (names[0] if names else "玩家")


def validate_vote(vote: str, suspects: list[str]) -> str | None:
    """校验投票：合法嫌疑人名字才返回，否则返回 None（弃权）。"""
    return vote if vote in suspects else None
