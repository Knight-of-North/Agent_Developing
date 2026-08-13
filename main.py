"""
AI 剧本杀主持人 · 入口（Phase 2：自动多轮讨论）

运行方式：python main.py
"""
from graph import build_graph

if __name__ == "__main__":
    graph = build_graph()

    theme = input("请输入剧本杀主题（回车使用默认「民国豪门恩怨」）：").strip() or "民国豪门恩怨"

    print(f"\n正在生成剧本，主题：{theme} ...\n")

    # invoke：把初始状态喂给图，它会自己跑完"开场 -> 循环讨论 -> 揭晓"整条流程
    result = graph.invoke({"theme": theme, "messages": [], "thoughts": []})

    # ---- 打印剧本 ----
    script = result.get("script", {})
    print("=" * 46)
    print("【案件背景】")
    print(script.get("background", script.get("raw", "（未生成）")))
    print("\n【嫌疑人】")
    for s in script.get("suspects", []):
        print(f"  · {s.get('name', '?')} —— {s.get('secret', '')}")
    print("=" * 46)

    # ---- 打印完整公开对话 ----
    print("\n【游戏过程】")
    for m in result.get("messages", []):
        print(f"\n{m['speaker']}: {m['content']}")

    # ---- 打印 AI 内心戏（think 通道，正式版不公开，这里仅供你调试）----
    print("\n" + "=" * 46)
    print("【AI 玩家内心戏】（调试用，正式版不公开）")
    for t in result.get("thoughts", []):
        print(f"  · {t}")
