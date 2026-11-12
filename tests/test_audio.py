"""声音系统静态契约测试（不依赖浏览器 / 不发声）。

音频本身由 Web Audio 在浏览器里合成，pytest 无法直接听；这里锁住三类最容易回归的点：
1. 关卡音效与 BGM 场景不能被误删（引擎里必须仍有对应定义）；
2. 控制面板必须保留音乐 / 音效双开关；
3. 雷雨音效必须彻底移除，阶段幕布不能再各自创建 AudioContext（否则音效会重复）。
"""
import re
from pathlib import Path

import ui_assets
import ui_audio

_APP_SOURCE = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")


def test_audio_engine_defines_stage_cues():
    html = ui_audio._AUDIO_ENGINE_HTML
    for cue in [
        "case_open", "intro", "role_reveal", "discuss_start", "turn",
        "vote_start", "tiebreak", "reveal", "win", "lose",
    ]:
        assert f"{cue}: function" in html


def test_audio_engine_defines_bgm_scenes():
    html = ui_audio._AUDIO_ENGINE_HTML
    for scene in ["menu", "discuss", "vote", "reveal", "ending_win", "ending_lose"]:
        assert f"{scene}: {{" in html
    # Minecraft 风格是默认 BGM 模式，必须有独立场景表与钢琴音色
    assert "MC_SCENES" in html
    assert "Minecraft" in html
    assert "style: 'minecraft'" in html
    assert "function piano(" in html


def test_audio_panel_has_bgm_and_sfx_controls():
    html = ui_audio._AUDIO_PANEL_HTML
    assert 'data-audio-act="bgm"' in html
    assert 'data-audio-act="sfx"' in html
    assert 'data-audio-act="style"' in html
    assert 'id="aap-vol"' in html


def test_legacy_thunderstorm_audio_is_gone():
    # 只检查可执行音频资源；ui_audio 模块 docstring 的"无雷雨"说明不算
    combined = ui_audio._AUDIO_ENGINE_HTML + ui_audio._AUDIO_PANEL_HTML
    for word in ["雷雨", "thunder", "rain"]:
        assert word not in combined
    # 视觉 iframe 不自己创建 AudioContext（避免重复音效 / 被浏览器拦截）
    assert "AudioContext" not in ui_assets._INTRO_HTML
    assert "AudioContext" not in ui_assets._PHASE_BANNER_HTML


def test_phase_banner_is_visual_only():
    # 音效统一由 ui_audio 顶层引擎播放，iframe 内不能再建 AudioContext
    assert "AudioContext" not in ui_assets._PHASE_BANNER_HTML
    assert "bannerIn" in ui_assets._PHASE_BANNER_HTML


def test_app_wires_all_stage_audio_hooks():
    # 防止 app.py 大改时静默丢掉声音接入点
    assert "init_audio_engine()" in _APP_SOURCE
    assert "render_audio_panel()" in _APP_SOURCE
    assert 'set_audio_scene("discuss")' in _APP_SOURCE
    assert 'set_audio_scene("vote")' in _APP_SOURCE
    assert re.search(r'play_audio_cue\(\s*"turn"', _APP_SOURCE)
    assert re.search(r'play_audio_cue\(\s*"tiebreak"', _APP_SOURCE)
    assert re.search(r'play_audio_cue\(\s*"reveal"', _APP_SOURCE)
    assert re.search(r'play_audio_cue\(\s*"win" if _player_won else "lose"', _APP_SOURCE)
