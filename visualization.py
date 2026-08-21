"""
人物关系图可视化（报告🟢#19 / 创意 5 / 8-18/8-19 多轮优化版）。

用 pyvis 把剧本里的 relations 字段渲染成交互式关系图（纯前端 HTML，
无需后端服务），嵌在 Streamlit 模态框。

信息差原则：只显示公开关系（public=true），私密关系（public=false）
不显示——否则把"谁和谁有秘密纠葛"直接画出来，等于剧透。

8-18 优化重点（针对飞哥反馈"边标签放不下 / 节点显示不出"）：
1. 节点从圆形改 box，width/height 显式控制，中文名字完整显示
2. 边标签字号 12→16，中文可读
3. 边标签 background 半透明白底，防互相压字
4. 边标签 align horizontal（水平显示），不再沿边方向旋转成竖排
5. 边标签截断到 8 字（超长 hover 看完整 title），避免短边塞长文字
6. 物理布局从 forceAtlas2Based 换 barnesHut（更稳）+ 强稳定轮预跑
7. springLength 100→220，给标签留出空间
8. 边用 curvedCW 弧形（不交叉，节点布局更舒展）
9. canvas height 360→600，模态框 640 内全展示
10. font 用 Microsoft YaHei，中文渲染更稳

8-18 第二轮（飞哥反馈"加箭头 + 省略我/他"）：
11. directed=True 有向图，箭头方向契合"我对他"的关系语义
12. 标签里删"我/他/她"（"我们"保留）—— 边方向已表明 from/to
13. 完整 rel 进 title，hover 看到原始第一人称描述

8-19 第三轮（飞哥反馈"多条边汇聚到一点 label 重叠"）：
14. springLength 220→280，给边更多空间让 label 分开
15. centralGravity 0.25→0.1，减弱中心引力让节点散开（避免汇聚）
16. avoidOverlap 0.3→0.5，更强分离避免堆叠
17. canvas height 600→680，模态框 720 内全展示不滚动
18. **奇偶边交错弯曲**：每条 add_edge 单独设 roundness——奇数边 roundness=+0.20
    偶数边 roundness=-0.20，汇聚到同一节点的多条边 label 物理位置错开，
    不再在中点堆叠（飞哥截图里"赵思远抢走了我的奖..."和"讨厌聂橙..."重叠
    就是因为所有边同向弯曲、中点挤在一起）

8-20 第四轮优化（飞哥反馈"左下角和右下角按钮被遮挡"）：
19. 关闭 navigationButtons（vis.js 默认的左下"重置视图"/右下"全屏"按钮）。
    这些是 vis.js 高级功能，普通玩家用滚轮缩放 + 拖拽就够，开了反而
    被模态框边缘截断一半（飞哥截图：左下/右下的绿色圆按钮各被挡一半）。
    改 interaction.navigationButtons = false，模态框干净、按钮不再生成。
"""
import re

from pyvis.network import Network


# 边标签最大字符数（中文按 1 字符计）。超过则截断加省略号，
# 完整内容放 title 悬浮显示——短边上能展示关键动作词，详情 hover 看。
EDGE_LABEL_MAX = 8


def _clean_pronouns(text: str) -> str:
    """去掉 rel 里的第一人称/指代代词（图标签更精炼）。

    为什么不留"我"？边从 from 指向 to，from 已知是"我"——再写"我"就是
    冗余。同理 to 已知是"他/她"——也删。完整 rel 仍进 title，hover 可看。

    为什么不删"我们"？"我们"是复数第一人称，语义是"双方都有这层关系"，
    边方向是 from→to 单一指向，删"我们"反而会失真。

    实现：用正则避免 .replace() 误删"我们"里的"我"——
    "我"后面紧跟"们"时保留，否则删除；"他/她"独立成词删除。
    """
    if not text:
        return ""
    # "我" 后面不跟"们"才删（保住"我们"）
    text = re.sub(r"我(?!们)", "", text)
    # "他/她" 直接删
    text = text.replace("他", "").replace("她", "")
    return text.strip()


def _truncate_label(text: str, max_len: int = EDGE_LABEL_MAX) -> str:
    """边标签截断：超过 max_len 字用省略号，完整内容在 title 悬浮显示。"""
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def build_relations_html(relations: list, suspect_names: list[str]) -> str:
    """生成人物关系图 HTML 字符串（只含公开关系）。

    - 节点：所有嫌疑人（蓝色矩形 + 白字）+ 死者（红色星形）
    - 边：公开关系（public=true），有向（from→to 带箭头），标签先删
      "我/他/她"再截断到 EDGE_LABEL_MAX 字，完整内容 hover 看 title
    - 无公开关系时返回空串（调用方据此决定是否渲染）

    防御（8-18 已修崩溃）：
    LLM 可能在 relations 里引用名单外角色（如背景设定里提到的路人），
    pyvis 的 add_edge 对不存在的节点会直接 AssertionError 把整个页面打崩。
    所以这里只保留"两端都在节点集合（嫌疑人 ∪ 死者）内"的边，名单外角色一律跳过。
    """
    # 过滤出公开关系
    public_rels = [
        r for r in relations
        if isinstance(r, dict) and r.get("public") and r.get("from") and r.get("to")
    ]
    if not public_rels:
        return ""

    # 节点集合：嫌疑人 + 死者。关系边两端必须都在集合内才能画，
    # 否则 pyvis 断言崩溃（LLM 把背景人物写进 relations 时必踩）。
    node_set = set(suspect_names) | {"死者"}

    # canvas 高度从 600 提到 680，配合模态框 720 让图整体舒展。
    net = Network(
        height="680px",
        width="100%",
        directed=True,           # 有向图：边从 from 指向 to（8-18 第二轮优化）
        bgcolor="#ffffff",
        font_color="#1f1f1f",
        heading="",
    )

    # 节点：嫌疑人用 box 矩形
    for name in suspect_names:
        net.add_node(
            name,
            label=name,
            shape="box",
            width=110,
            height=48,
            color={"background": "#5b8ff9", "border": "#3a6fd4", "highlight": {"background": "#3a6fd4", "border": "#1f4ea8"}},
            font={"color": "#ffffff", "size": 20, "face": "Microsoft YaHei", "strokeWidth": 0},
            title=f"<b>{name}</b><br/>嫌疑人",
            borderWidth=2,
            margin=8,
        )

    # 节点：死者用红色星形 + fixed 居中
    # 飞哥 17:17 建议"以死者为中心"：fixed x/y 钉死中心，physics 不会挪动它，
    # 嫌疑人围绕它做物理布局；mass=2 让嫌疑人被它吸引（比之前的 mass=3 温和，
    # 飞哥上一轮反对过"紧张主题"）。x=0/y=0 是 vis.js 初始坐标，fit 后会被
    # 居中到 canvas。
    net.add_node(
        "死者",
        label="死者",
        shape="star",
        size=40,
        mass=2,
        x=0, y=0,
        fixed={"x": True, "y": True},
        color={"background": "#f5222d", "border": "#a8071a", "highlight": {"background": "#a8071a", "border": "#5b0000"}},
        font={"color": "#ffffff", "size": 22, "face": "Microsoft YaHei", "strokeWidth": 3, "strokeColor": "#a8071a"},
        title="<b>死者</b><br/>案件核心",
    )

    # 边：公开关系（只画两端都在节点集合内的边，名单外角色跳过，防崩溃）
    # 8-19 第三轮：奇偶边交错弯曲（roundness ±0.20），汇聚到同一节点的多条
    # 边 label 物理位置错开，避免中点堆叠
    for idx, r in enumerate(public_rels):
        if r["from"] not in node_set or r["to"] not in node_set:
            continue
        rel = r.get("rel", "")
        display = _truncate_label(_clean_pronouns(rel))
        # 奇数边右凸（CW）、偶数边左凸（CCW），同源/同汇的边交错开
        roundness = 0.20 if idx % 2 == 0 else -0.20
        smooth_type = "curvedCW" if idx % 2 == 0 else "curvedCCW"
        net.add_edge(
            r["from"], r["to"],
            label=display,
            title=f"<b>{r['from']}</b> → <b>{r['to']}</b><br/>{rel}",
            color={"color": "#999999", "highlight": "#f5222d"},
            width=2,
            font={"size": 16, "color": "#1f1f1f", "face": "Microsoft YaHei",
                  "align": "horizontal",
                  "background": "rgba(255,255,255,0.92)",
                  "strokeWidth": 4, "strokeColor": "#ffffff"},
            smooth={"enabled": True, "type": smooth_type, "roundness": roundness},
        )

    # 物理布局：节点散开 + 强稳定 + 长弹簧
    # 8-19 第三轮：springLength 220→280,centralGravity 0.25→0.1,avoidOverlap 0.3→0.5
    net.set_options("""
    var options = {
      "layout": {
        "improvedLayout": true,
        "hierarchical": {"enabled": false}
      },
      "physics": {
        "enabled": true,
        "stabilization": {
          "enabled": true,
          "iterations": 400,
          "fit": true,
          "updateInterval": 25
        },
        "solver": "barnesHut",
        "barnesHut": {
          "gravitationalConstant": -2400,
          "centralGravity": 0.1,
          "springLength": 280,
          "springConstant": 0.035,
          "damping": 0.5,
          "avoidOverlap": 0.5
        },
        "minVelocity": 0.75
      },
      "nodes": {
        "borderWidth": 2,
        "shapeProperties": {"interpolation": false},
        "chosen": true
      },
      "edges": {
        "width": 2,
        "smooth": {"enabled": true, "type": "dynamic", "roundness": 0.2},
        "chosen": true,
        "arrows": {"to": {"enabled": true, "scaleFactor": 0.6}}
      },
      "interaction": {
        "hover": true,
        "tooltipDelay": 100,
        "navigationButtons": false,
        "keyboard": false,
        "zoomView": true,
        "dragView": true
      }
    }
    """)
    return net.generate_html()