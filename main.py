"""
AI 剧本杀主持人 · 入口（Phase 5：用户选择角色，interrupt 人在回路）

运行方式：python main.py
"""
import uuid
from nodes import _parse_names
from interrupt_handler import validate_vote, is_abstain
from game_session import GameSession
from logging_config import setup_logging, set_game_id
from config import (
    MAX_THEME_LEN, MAX_STORY_TIME_LEN, MAX_STORY_LOCATION_LEN,
    MAX_BG_STORY_LEN, MAX_CHAT_LEN, INVESTIGATE_LIMIT,
)

import logging

setup_logging()
logger = logging.getLogger(__name__)


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
    # H4：终端入口接入 GameSession（与 app.py 共享会话抽象层，不再各写一套 invoke/config）。
    # thread_id 用 uuid4：写死 "murder_1" 时两次对局共享同一 checkpoint，
    # 旧局的 votes/messages 残留进新局（合并 reducer 语义下直接串档）。
    sess = GameSession(thread_id=str(uuid.uuid4()))
    # M12：把对局 ID 注入日志上下文，logs/game.log 里可按 g=xxxxxx 整局过滤
    set_game_id(sess.thread_id)

    theme = (input("请输入剧本杀主题（回车自由发挥）：").strip() or "自由发挥")[:MAX_THEME_LEN]
    background = input("背景风格（回车自由发挥，可选：民国豪门/校园怪谈/古风仙侠/现代都市/科幻末世）：").strip() or "自由发挥"
    background_story = input("自定义剧情背景（回车跳过，让 AI 自由发挥；填写则 AI 理解后融入创作）：").strip()[:MAX_BG_STORY_LEN]
    story_time = input("故事发生时间（回车跳过，如：1935年深秋 / 宋代江南）：").strip()[:MAX_STORY_TIME_LEN]
    story_location = input("故事发生地点（回车跳过，如：上海滩租界 / 湖南师大图书馆）：").strip()[:MAX_STORY_LOCATION_LEN]
    custom_names = _parse_names(input("自定义嫌疑人名字（回车跳过用随机；填写如：张三,李四,王五）："))
    # F2：名字白名单校验，过滤含特殊字符的名字
    import re as _re
    custom_names = [n for n in custom_names if _re.fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9·]{2,10}", n or "")]
    # 讨论节奏（8-20 审查修复：与 Web 版对齐，终端版也可调快/标准/深入 → 每人 2/3/4 轮）
    pace = input("讨论节奏（回车标准；快=每人2轮 / 标准=每人3轮 / 深入=每人4轮）：").strip()
    rounds_per_player = {"快": 2, "深入": 4}.get(pace, 3)
    print(f"\n正在生成剧本，主题：{theme}，背景：{background} ...\n")

    # H3：所有 invoke 包一层异常防护。LLM 断连/超时/解析失败时图状态仍停在原 interrupt，
    # 重试同一 payload 是安全的；用户也可选择退出。
    def safe_resume(payload):
        while True:
            try:
                return sess.resume_invoke(payload)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                logger.error("图执行失败：%s", e, exc_info=True)
                print(f"\n⚠️ 执行出错（{type(e).__name__}）：{e}")
                choice = input("输入 r 重试，其他键退出：").strip().lower()
                if choice != "r":
                    print("已退出当前对局。")
                    raise SystemExit(1)

    # 第一次调用：跑到第一个 interrupt（选角色）时暂停
    try:
        result = sess.start_invoke(
            {"theme": theme, "background_style": background, "background_story": background_story,
             "story_time": story_time, "story_location": story_location, "custom_names": custom_names,
             "rounds_per_player": rounds_per_player, "messages": [], "thoughts": []}
        )
    except KeyboardInterrupt:
        raise
    except Exception as e:
        logger.error("剧本生成失败：%s", e, exc_info=True)
        print(f"\n⚠️ 剧本生成失败（{type(e).__name__}）：{e}")
        raise SystemExit(1)

    # ---- 边玩边打印：只打印"新增"的消息 ----
    printed = 0

    def show_new(messages, start):
        """打印从 start 开始的新消息，返回最新长度"""
        for m in messages[start:]:
            print(f"\n{m['speaker']}: {m['content']}")
        return len(messages)

    # ---- 循环处理 interrupt：图暂停时，读提示 -> 用户输入 -> 恢复 ----
    while not sess.is_finished:
        info = sess.interrupt_info
        if not info:
            break

        if info["type"] == "choose_role":
            # 开局选角色：打印名单让用户挑
            suspects_list = info["suspects"]
            print("\n" + "=" * 46)
            print("【选择角色】你想扮演哪个嫌疑人？")
            for i, n in enumerate(suspects_list, 1):
                print(f"  {i}. {n}")
            print("=" * 46)

            # R9：非法输入（错别字/名单外名字/空输入）循环重问，直到合法——
            # 此前非法值被静默回退 names[0]，玩家会莫名其妙扮演第一个嫌疑人。
            chosen = ""
            while chosen not in suspects_list:
                chosen = input("输入名字或序号：").strip()
                # 支持按序号选：输入 1/2/3 也能对应到名单
                if chosen.isdigit() and 1 <= int(chosen) <= len(suspects_list):
                    chosen = suspects_list[int(chosen) - 1]
                if chosen not in suspects_list:
                    print(f"  ⚠️ 「{chosen}」不在嫌疑人名单里，请输入名单中的名字或序号")
            result = safe_resume(chosen)

            # 选完角色后，user_role 才确定，此时打印角色卡 + 已发生的消息（DM 开场 / AI 发言）
            print_role_card(result)
            printed = show_new(result.get("messages", []), printed)

        elif info["type"] == "human_turn":
            # 玩家回合：支持发言 + 公开线索/指控/调查三种行动（终端版用菜单选择）
            own_clues = info.get("own_clues", [])
            targets = info.get("targets", [])
            can_investigate = info.get("can_investigate", False)
            inv_remaining = info.get("investigations_remaining", INVESTIGATE_LIMIT)

            print(f"\n【轮到你了·{info['speaker']}】你可以：")
            print("  · 直接输入文字 = 发言")
            print("  · 输入「沉默」= 保持缄默（这一轮不发言）")
            if own_clues:
                print("  · 输入 2 = 公开一条你的私密线索")
            if targets:
                print("  · 输入 3 = 指控某人是凶手")
            if can_investigate:
                print(f"  · 输入 4 = 调查现场（剩余 {inv_remaining} 次）")

            # R14：行动菜单循环——未定义的数字序号（如 1/5）提示重输，
            # 不再被默认分支当成公开发言发出去。
            while True:
                user_input = input("你的行动：").strip()

                if user_input == "2" and own_clues:
                    print("  你的私密线索：")
                    for i, c in enumerate(own_clues, 1):
                        print(f"    {i}. {c}")
                    # L2：序号非法时重新提示，而不是把"2"当发言发出去
                    while True:
                        choice = input("  公开哪条（输入序号，回车取消）：").strip()
                        if choice == "":
                            resume = input("你的发言：").strip()[:MAX_CHAT_LEN]
                            break
                        if choice.isdigit() and 1 <= int(choice) <= len(own_clues):
                            resume = GameSession.build_resume("reveal_clue", clue=own_clues[int(choice) - 1])
                            break
                        print(f"  ⚠️ 请输入 1~{len(own_clues)} 之间的序号")
                    break
                elif user_input == "3" and targets:
                    print(f"  可指控对象：{', '.join(targets)}")
                    while True:
                        target = input("  指控谁（输入名字，回车取消）：").strip()
                        if target == "":
                            resume = input("你的发言：").strip()[:MAX_CHAT_LEN]
                            break
                        if target in targets:
                            resume = GameSession.build_resume("accuse", target=target)
                            break
                        print(f"  ⚠️ 「{target}」不在可指控名单里")
                    break
                elif user_input == "4" and can_investigate:
                    resume = GameSession.build_resume("investigate")
                    break
                elif user_input.isdigit():
                    print("  ⚠️ 无此选项，请输入发言文字，或按菜单输入对应序号")
                    continue
                else:
                    resume = user_input[:MAX_CHAT_LEN]   # 默认当发言（F2：截断超长输入）
                    break

            result = safe_resume(resume)
            printed = show_new(result.get("messages", []), printed)

        elif info["type"] == "human_vote":
            # 轮到你投票（校验输入必须是合法嫌疑人名字；M9：也可输入「弃权」放弃投票）
            suspects_list = info["suspects"]
            print(f"\n【投票】嫌疑人名单：{', '.join(suspects_list)}")
            print("  （输入「弃权」可放弃本轮投票）")
            user_input = input("你投谁（输入名字）：").strip()
            while not is_abstain(user_input) and not validate_vote(user_input, suspects_list):
                print(f"  ⚠️ 无效投票，请从名单里选：{', '.join(suspects_list)}")
                user_input = input("你投谁（输入名字）：").strip()
            result = safe_resume(user_input)
            # 投票后图会跑 tally -> dm_reveal，打印 DM 揭晓台词
            printed = show_new(result.get("messages", []), printed)

    # ---- 打印最终结算 ----
    print("\n" + "=" * 46)
    print("【投票结果】")
    for voter, target in result.get("votes", {}).items():
        print(f"  · {voter} → {target or '（弃权）'}")
    print(f"\n得票最多：{result.get('vote_winner', '无人')}")
    print(f"票数分布：{result.get('vote_counts', {})}")
    print("=" * 46)
    sess.cleanup()
