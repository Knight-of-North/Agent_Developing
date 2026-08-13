"""
AI 剧本杀主持人 · 入口

运行方式：python main.py
"""
from graph import build_graph

if __name__ == "__main__":
    # 编译出可运行的图
    graph = build_graph()

    # 让用户输入主题（回车则用默认主题）
    theme = input("请输入剧本杀主题（回车使用默认「民国豪门恩怨」）：").strip() or "民国豪门恩怨"

    print(f"\n正在生成剧本，主题：{theme} ...\n")

    # invoke：把初始状态喂给图，让它从头跑到尾
    # 初始状态：主题 + 空的对话记录（messages 一定要给空列表，reducer 才能正常追加）
    result = graph.invoke({"theme": theme, "messages": []})

    # ---- 打印剧本 ----
    script = result.get("script", {})
    print("=" * 46)
    print("【案件背景】")
    print(script.get("background", script.get("raw", "（未生成）")))
    print("\n【嫌疑人】")
    for s in script.get("suspects", []):
        print(f"  · {s.get('name', '?')} —— {s.get('secret', '')}")
    print("\n【线索】")
    for c in script.get("clues", []):
        print(f"  · {c}")
    print("=" * 46)

    # ---- 打印 DM 台词 ----
    print("\n【主持人 DM】")
    for m in result.get("messages", []):
        print(m)
