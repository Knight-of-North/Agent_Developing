"""
AI 剧本杀主持人 · 入口（Phase 3.5：用户参与，interrupt 人在回路）

运行方式：python main.py
"""
from langgraph.types import Command
from graph import build_graph

if __name__ == "__main__":
    graph = build_graph()
    # thread_id 标识"这一局游戏"，每次调用都带上，图才知道接着上次跑
    config = {"configurable": {"thread_id": "murder_1"}}

    theme = input("请输入剧本杀主题（回车自由发挥）：").strip() or "自由发挥"
    background = input("背景风格（回车自由发挥，可选：民国豪门/校园怪谈/古风仙侠/现代都市/科幻末世）：").strip() or "自由发挥"
    print(f"\n正在生成剧本，主题：{theme}，背景：{background} ...\n")

    # 第一次调用：跑到第一个 interrupt（轮到你发言）时暂停
    result = graph.invoke(
        {"theme": theme, "background_style": background, "messages": [], "thoughts": []},
        config,
    )

    # ---- 打印你的角色卡（让你知道自己的秘密，才能带入角色）----
    script = result.get("script", {})
    user_role = result.get("user_role", "你")
    suspects = script.get("suspects", [])
    user_secret = next((s.get("secret", "") for s in suspects if s.get("name") == user_role), "")
    print("=" * 46)
    print(f"【你的角色卡】你扮演：{user_role}")
    print(f"  你的秘密（只能自己知道，别主动暴露）：{user_secret}")
    print(f"  目标：隐瞒秘密，同时找出真凶")
    print("=" * 46)

    # ---- 边玩边打印：只打印"新增"的消息 ----
    printed = 0

    def show_new(messages, start):
        """打印从 start 开始的新消息，返回最新长度"""
        for m in messages[start:]:
            print(f"\n{m['speaker']}: {m['content']}")
        return len(messages)

    # 先打印已经发生的（DM 开场）
    printed = show_new(result.get("messages", []), printed)

    # ---- 循环处理 interrupt：图暂停时，读提示 -> 用户输入 -> 恢复 ----
    while "__interrupt__" in result:
        info = result["__interrupt__"][0].value

        if info["type"] == "human_turn":
            # 轮到你发言
            user_input = input(f"\n【轮到你了·{info['speaker']}】请输入你的发言：")
            result = graph.invoke(Command(resume=user_input), config)
            printed = show_new(result.get("messages", []), printed)

        elif info["type"] == "human_vote":
            # 轮到你投票
            suspects_list = info["suspects"]
            print(f"\n【投票】嫌疑人名单：{', '.join(suspects_list)}")
            user_input = input("你投谁（输入名字）：").strip()
            result = graph.invoke(Command(resume=user_input), config)
            # 投票后图会跑 tally -> dm_reveal，打印 DM 揭晓台词
            printed = show_new(result.get("messages", []), printed)

    # ---- 打印最终结算 ----
    print("\n" + "=" * 46)
    print("【投票结果】")
    for voter, target in result.get("votes", {}).items():
        print(f"  · {voter} → {target}")
    print(f"\n得票最多：{result.get('vote_winner', '无人')}")
    print(f"票数分布：{result.get('vote_counts', {})}")
    print("=" * 46)

    print("\n【AI 玩家内心戏】（调试用，正式版不公开）")
    for t in result.get("thoughts", []):
        print(f"  · {t}")
