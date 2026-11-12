"""角色视觉层纯函数测试：头像确定性、HTML 转义、嫌疑人总览。"""
from ui_characters import avatar_data_uri, avatar_html, character_card_html, role_header_html, suspect_roster_html


def test_avatar_is_deterministic_and_uses_initial():
    first = avatar_html("舒飞", 64)
    second = avatar_html("舒飞", 64)
    assert first == second
    assert ">舒<" in first


def test_avatar_escapes_html_in_names():
    markup = avatar_html("<script>alert(1)</script>")
    assert "<script>" not in markup
    assert "&lt;" in markup


def test_character_card_escapes_llm_fields():
    card = character_card_html(
        "张三",
        "<img src=x onerror=alert(1)>",
        "与死者<b>关系</b>",
    )
    assert "<img" not in card
    # 卡片自身有一个 <b>关系</b> 标签；用户字段里的 <b> 必须被转义
    assert card.count("<b>关系</b>") == 1
    assert "&lt;b&gt;关系&lt;/b&gt;" in card


def test_role_header_and_roster_render_all_suspects():
    suspects = [
        {"name": "张三", "profession": "医生"},
        {"name": "李四", "profession": "管家"},
    ]
    roster = suspect_roster_html(suspects, "张三")
    assert "张三" in roster and "李四" in roster
    assert "roster-row me" in roster
    header = role_header_html("张三", "医生", "死者的弟弟")
    assert "role-hero" in header and "张三" in header


def test_avatar_data_uri_is_svg_data_uri():
    uri = avatar_data_uri("张三")
    assert uri.startswith("data:image/svg+xml;base64,")
