"""
人物关系图可视化（报告🟢#19 / 创意 5）。

用 pyvis 把剧本里的 relations 字段渲染成交互式关系图（纯前端 HTML，
无需后端服务），嵌在 Streamlit 侧边栏。

信息差原则：只显示公开关系（public=true），私密关系（public=false）
不显示——否则把"谁和谁有秘密纠葛"直接画出来，等于剧透。
"""
from pyvis.network import Network


def build_relations_html(relations: list, suspect_names: list[str]) -> str:
    """生成人物关系图 HTML 字符串（只含公开关系）。

    - 节点：所有嫌疑人（蓝色）+ 死者（红色星形，视觉突出）
    - 边：公开关系（public=true），标注关系内容；私密关系不显示
    - 无公开关系时返回空串（调用方据此决定是否渲染）

    返回的 HTML 供 Streamlit 的 st.components.html 直接嵌入。
    """
    # 过滤出公开关系
    public_rels = [
        r for r in relations
        if isinstance(r, dict) and r.get("public") and r.get("from") and r.get("to")
    ]
    if not public_rels:
        return ""

    net = Network(
        height="360px",
        width="100%",
        directed=False,          # 人物关系通常是双向的，用无向图
        bgcolor="#ffffff",
        font_color="#1f1f1f",
    )

    # 节点：嫌疑人（蓝色）
    for name in suspect_names:
        net.add_node(name, label=name, color="#5b8ff9", title=name, border_width=1)

    # 节点：死者（红色星形，一眼看出是案件中心）
    net.add_node("死者", label="死者", color="#f5222d", title="案件死者", shape="star", size=28)

    # 边：公开关系
    for r in public_rels:
        net.add_edge(
            r["from"], r["to"],
            label=r.get("rel", ""),
            title=f'{r["from"]} 与 {r["to"]}：{r.get("rel", "")}',
            color="#999999",
        )

    # 物理布局参数：让图更舒展（默认斥力较大，节点会挤在一起）
    net.set_options("""
    var options = {
      "physics": {
        "enabled": true,
        "solver": "forceAtlas2Based",
        "forceAtlas2Based": {"gravitationalConstant": -60, "springLength": 100}
      },
      "nodes": {"font": {"size": 16}},
      "edges": {"font": {"size": 12, "align": "middle"}}
    }
    """)

    return net.generate_html()
