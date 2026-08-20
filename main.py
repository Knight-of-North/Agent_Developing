"""
AI 剧本杀主持人 · 入口（Phase 5：用户选择角色，interrupt 人在回路）

运行方式：python main.py
"""
from langgraph.types import Command
from graph import build_graph
from nodes import _parse_names
from interrupt_handler import get_interrupt, validate_vote


def print_role_card(result):
    """打印角色卡：你的角色 + 秘密 + 私密线索（选完角色之后才能打印）"""
    script = result.get("script", {})
    user_role = result.get("user_role", "你")
    suspects = script.get("suspects", [])
    user_secret = next((s.get("secret", "") for s in suspects if s.get("name") == user_role), "")
    user_script = next((s.get("personal_script", "") for s in suspects if s.get("name") == user_role), "")
    user_suspect = next((s for s in suspects if s.get("name") == user_role), {})
    # 信息差：只显示你自己持有的私密线索，别人的线索你看不到
    user_clues = result.get("distributed_clues", {}).get(user_role, [])
    print("=" * 46)
    print(f"【你的角色卡】你扮演：{user_role}")
    if result.get("user_is_murderer"):
        print("  ⚠️ 你是真凶！你的目标：误导其他人、隐藏证据、别被投出去。")
    if user_suspect.get("profession"):
        print(f"  职业：{user_suspect.get('profession')}")
    if user_suspect.get("relation_to_victim"):
        print(f"  与死者的关系：{user_suspect.get('relation_to_victim')}")
    if user_suspect.get("alibi"):
        print(f"  不在场证明：{user_suspect.get('alibi')}")
    if user_suspect.get("task"):
        print(f"  你的任务：{user_suspect.get('task')}")
    if user_script:
        print(f"\n  【你的个人剧本】（开局先读，发言和推理都靠它）：\n  {user_script}")
    print(f"\n  你的秘密（只能自己知道，别主动暴露）：{user_secret}")
    print("  你掌握的私密线索（只有你知道，是否公开由你决定）：")
    if user_clues:
        for c in user_clues:
            print(f"    · {c}")
    else:
        print("    · （你没有任何私密线索，只能靠盘问别人）")
    print(f"  目标：隐瞒秘密，同时从别人嘴里套线索、找出真凶")
    print("=" * 46)


if __name__ == "__main__":
    graph = build_graph()
    # thread_id 标识"这一局游戏"，每次调用都带上，图才知道接着上次跑
    config = {"configurable": {"thread_id": "murder_1"}}

    theme = input("请输入剧本杀主题（回车自由发挥）：").strip() or "自由发挥"
    background = input("背景风格（回车自由发挥，可选：民国豪门/校园怪谈/古风仙侠/现代都市/科幻末世）：").strip() or "自由发挥"
    background_story = input("自定义剧情背景（回车跳过，让 AI 自由发挥；填写则 AI 理解后融入创作）：").strip()
    story_time = input("故事发生时间（回车跳过，如：1935年深秋 / 宋代江南）：").strip()
    story_location = input("故事发生地点（回车跳过，如：上海滩租界 / 湖南师大图书馆）：").strip()
    custom_names = _parse_names(input("自定义嫌疑人名字（回车跳过用随机；填写如：张三,李四,王五）："))
    print(f"\n正在生成剧本，主题：{theme}，背景：{background} ...\n")

    # 第一次调用：跑到第一个 interrupt（选角色）时暂停
    result = graph.invoke(
        {"theme": theme, "background_style": background, "background_story": background_story, "story_time": story_time, "story_location": story_location, "custom_names": custom_names, "messages": [], "thoughts": []},
        config,
    )

    # ---- 边玩边打印：只打印"新增"的消息 ----
    printed = 0

    def show_new(messages, start):
        """打印从 start 开始的新消息，返回最新长度"""
        for m in messages[start:]:
            print(f"\n{m['speaker']}: {m['content']}")
        return len(messages)

    # ---- 循环处理 interrupt：图暂停时，读提示 -> 用户输入 -> 恢复 ----
    while get_interrupt(result):
        info = get_interrupt(result)

        if info["type"] == "choose_role":
            # 开局选角色：打印名单让用户挑
            suspects_list = info["suspects"]
            print("\n" + "=" * 46)
            print("【选择角色】你想扮演哪个嫌疑人？")
            for i, n in enumerate(suspects_list, 1):
                print(f"  {i}. {n}")
            print("=" * 46)
            chosen = input("输入名字或序号：").strip()
            # 支持按序号选：输入 1/2/3 也能对应到名单
            if chosen.isdigit() and 1 <= int(chosen) <= len(suspects_list):
                chosen = suspects_list[int(chosen) - 1]
            result = graph.invoke(Command(resume=chosen), config)

            # 选完角色后，user_role 才确定，此时打印角色卡 + 已发生的消息（DM 开场 / AI 发言）
            print_role_card(result)
            printed = show_new(result.get("messages", []), printed)

        elif info["type"] == "human_turn":
            # 玩家回合：支持发言 + 公开线索/指控/调查三种行动（终端版用菜单选择）
            own_clues = info.get("own_clues", [])
            targets = info.get("targets", [])
            can_investigate = info.get("can_investigate", False)

            print(f"\n【轮到你了·{info['speaker']}】你可以：")
            print("  · 直接输入文字 = 发言")
            if own_clues:
                print("  · 输入 2 = 公开一条你的私密线索")
            if targets:
                print("  · 输入 3 = 指控某人是凶手")
            if can_investigate:
                print("  · 输入 4 = 调查现场（发现隐藏线索）")

            user_input = input("你的行动：").strip()

            if user_input == "2" and own_clues:
                print("  你的私密线索：")
                for i, c in enumerate(own_clues, 1):
                    print(f"    {i}. {c}")
                choice = input("  公开哪条（输入序号）：").strip()
                if choice.isdigit() and 1 <= int(choice) <= len(own_clues):
                    resume = {"action": "reveal_clue", "clue": own_clues[int(choice) - 1]}
                else:
                    resume = user_input   # 非法序号，退回当发言
            elif user_input == "3" and targets:
                print(f"  可指控对象：{', '.join(targets)}")
                target = input("  指控谁（输入名字）：").strip()
                resume = {"action": "accuse", "target": target}
            elif user_input == "4" and can_investigate:
                resume = {"action": "investigate"}
            else:
                resume = user_input   # 默认当发言

            result = graph.invoke(Command(resume=resume), config)
            printed = show_new(result.get("messages", []), printed)

        elif info["type"] == "human_vote":
            # 轮到你投票（校验输入必须是合法嫌疑人名字）
            suspects_list = info["suspects"]
            print(f"\n【投票】嫌疑人名单：{', '.join(suspects_list)}")
            user_input = input("你投谁（输入名字）：").strip()
            while not validate_vote(user_input, suspects_list):
                print(f"  ⚠️ 无效投票，请从名单里选：{', '.join(suspects_list)}")
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
