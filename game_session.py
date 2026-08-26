"""
游戏会话抽象层（F9）—— 封装 LangGraph 图的生命周期、interrupt 提取与 resume。

之前 app.py（Streamlit）和 main.py（终端）各自维护：
  - thread_id / config 构造
  - graph.invoke / graph.stream 调用
  - interrupt 提取与类型分发
  - 结束判断
新增一种行动或 interrupt 类型要改两处。本类把这些收口，UI 层只负责渲染和采集输入。

用法（非流式，终端）：
    sess = GameSession()
    state = sess.start_invoke(initial_state)
    while not sess.is_finished:
        info = sess.interrupt_info
        payload = ui_collect(info)
        state = sess.resume_invoke(payload)

用法（流式，Web）：
    sess = GameSession()
    for chunk in sess.start_stream(initial_state, stream_mode=["messages","updates"]):
        render(chunk)
    while not sess.is_finished:
        payload = ui_collect(sess.interrupt_info)
        for chunk in sess.resume_stream(payload, stream_mode=["messages","updates"]):
            render(chunk)
"""
from __future__ import annotations

import uuid
from typing import Any, Iterator

from langgraph.types import Command
from langgraph.errors import GraphInterrupt

from graph import build_graph


class GameSession:
    def __init__(self, thread_id: str | None = None):
        self.graph = build_graph()
        self.thread_id = thread_id or str(uuid.uuid4())
        self.config = {"configurable": {"thread_id": self.thread_id}}

    # ---------- 状态访问 ----------

    @property
    def state(self) -> dict:
        """当前完整 state（从 checkpointer 读取最新快照）。"""
        return self.graph.get_state(self.config).values

    @property
    def interrupt_info(self) -> dict | None:
        """当前 interrupt 信息；无 interrupt（游戏结束或未暂停）返回 None。"""
        snapshot = self.graph.get_state(self.config)
        if not snapshot or not snapshot.tasks:
            return None
        for task in snapshot.tasks:
            if task.interrupts:
                return task.interrupts[0].value
        return None

    @property
    def is_finished(self) -> bool:
        """图是否已走到 END（无待处理 interrupt 且无 next 节点）。"""
        snapshot = self.graph.get_state(self.config)
        if snapshot is None:
            return False
        # 有 interrupt 说明在等人
        if snapshot.tasks and any(t.interrupts for t in snapshot.tasks):
            return False
        return not snapshot.next

    # ---------- 启动 ----------

    def _initial_input(self, initial_state: dict | None) -> dict:
        base = dict(initial_state or {})
        base.setdefault("rounds_per_player", 3)
        return base

    def start_invoke(self, initial_state: dict | None = None) -> dict:
        """开局并跑到第一个 interrupt（通常是 choose_role），返回 state。"""
        self.graph.invoke(self._initial_input(initial_state), config=self.config)
        return self.state

    def start_stream(self, initial_state: dict | None = None, stream_mode=None) -> Iterator[Any]:
        """开局并流式产出 chunk（跑到第一个 interrupt）。"""
        return self.graph.stream(self._initial_input(initial_state), config=self.config,
                                 stream_mode=stream_mode or "updates")

    # ---------- 恢复 ----------

    def resume_invoke(self, payload: Any) -> dict:
        """用玩家输入恢复执行，跑到下一个 interrupt 或 END，返回 state。"""
        self.graph.invoke(Command(resume=payload), config=self.config)
        return self.state

    def resume_stream(self, payload: Any, stream_mode=None) -> Iterator[Any]:
        """用玩家输入恢复执行并流式产出 chunk。"""
        return self.graph.stream(Command(resume=payload), config=self.config,
                                 stream_mode=stream_mode or "updates")

    # ---------- 工具 ----------

    def cleanup(self) -> None:
        """清理当前 thread 的 checkpoint（MemorySaver 下释放内存，H20）。"""
        try:
            self.graph.checkpointer.delete(self.thread_id)
        except Exception:
            pass

    @staticmethod
    def build_resume(action: str, **kwargs) -> dict:
        """统一构造 human_turn 的 action dict（app.py/main.py 共用，避免手写两份）。

        例：GameSession.build_resume("reveal_clue", clue="...")
            GameSession.build_resume("accuse", target="李四")
            GameSession.build_resume("investigate")
        """
        return {"action": action, **kwargs}
