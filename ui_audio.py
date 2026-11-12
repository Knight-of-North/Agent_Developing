"""游戏音效与背景音乐引擎（Web Audio 纯代码合成，无外部音频素材）。

设计参考（狼人杀 / 剧本杀常见做法）：
- BGM 按阶段切换：候场 → 讨论 → 投票 → 揭晓/结局，音量压低、不抢台词；
- 每个流程节点配独立提示音：翻卷宗、盖章、开场钟声、轮到你、投票、法槌、胜负；
- 音乐与音效分开控制，音量持久化，浏览器首次交互后自动解锁音频。

Minecraft 风格模式：
- 参考 C418 为《Minecraft》创作的极简钢琴 / 氛围音乐气质（稀疏音符、长混响、
  温暖低频垫、留白），但旋律与和弦进行均为本项目原创，不使用、不内嵌 Minecraft 原曲
  （原曲受版权保护，不能随项目分发）；
- 控制面板可一键在「Minecraft 风格」与「悬疑档案」之间切换，各阶段 BGM 会自动重排。

实现要点：
- 只用 Web Audio API 合成（振荡器 + 噪声 + 卷积混响），不下载、不打包任何 mp3，
  避免素材版权问题，也让音频状态完全由代码掌控；
- 引擎挂在页面顶层 window 上（`st.html` 注入，非 iframe），Streamlit rerun 时
  AudioContext 不重建，BGM 不闪断；
- 每个音效调用带 token 去重，页面重复渲染不会重复响；
- 音色以档案悬疑为底，Minecraft 风格额外使用原创钢琴 / 暖垫；无雷雨、无真实采样，
  只用弦垫、音乐盒、钢琴、钟鼓、法槌等。
"""
import json
import time

import streamlit as st

# ==================== 音频引擎（注入页面顶层，只初始化一次） ====================
_AUDIO_ENGINE_HTML = """
<div id="agent-audio-engine" style="display:none"></div>
<script>
(function () {
  if (window.__agentAudio) { return; }

  var STORE_KEY = 'agent_developing.audio.v1';
  var DEFAULT_SETTINGS = { bgm: true, sfx: true, volume: 0.55, style: 'minecraft' };
  var settings = {};
  try {
    var saved = JSON.parse(localStorage.getItem(STORE_KEY) || '{}');
    settings = {
      bgm: saved.bgm !== false,
      sfx: saved.sfx !== false,
      volume: (typeof saved.volume === 'number') ? Math.max(0, Math.min(1, saved.volume)) : DEFAULT_SETTINGS.volume,
      style: saved.style === 'suspense' ? 'suspense' : 'minecraft'
    };
  } catch (e) {
    settings = { bgm: true, sfx: true, volume: DEFAULT_SETTINGS.volume, style: DEFAULT_SETTINGS.style };
  }

  // ---------- 场景配置：D 小调为主，情绪从克制到紧张 ----------
  // chords: 每小节和弦（MIDI），第一个音是低音根音；melody: 旋律音池（MIDI）
  var SCENES = {
    menu: {
      bpm: 56,
      chords: [[38, 50, 53, 57], [34, 46, 50, 53], [31, 43, 46, 50], [33, 45, 49, 52]],
      melody: [62, 65, 69, 72, 74, 77],
      density: 0.44, pad: 0.040, tick: 0, pulse: false
    },
    discuss: {
      bpm: 64,
      chords: [[38, 50, 53, 57], [34, 46, 50, 53], [41, 53, 57, 60], [36, 48, 52, 55]],
      melody: [62, 65, 69, 72, 74, 77, 81],
      density: 0.55, pad: 0.036, tick: 0.035, pulse: false
    },
    vote: {
      bpm: 84,
      chords: [[38, 50, 53, 56], [39, 51, 55, 58], [38, 50, 53, 57], [33, 45, 49, 52]],
      melody: [62, 63, 69, 70, 74],
      density: 0.40, pad: 0.032, tick: 0.055, pulse: true
    },
    reveal: {
      bpm: 52,
      chords: [[34, 46, 50, 53], [31, 43, 46, 50], [38, 50, 53, 57], [33, 45, 49, 52]],
      melody: [62, 65, 69, 74],
      density: 0.30, pad: 0.045, tick: 0, pulse: false
    },
    ending_win: {
      bpm: 58,
      chords: [[38, 50, 54, 57], [43, 55, 58, 62], [45, 57, 60, 64], [38, 50, 54, 57]],
      melody: [62, 66, 69, 74, 78],
      density: 0.36, pad: 0.040, tick: 0, pulse: false
    },
    ending_lose: {
      bpm: 50,
      chords: [[38, 50, 53, 57], [34, 46, 50, 53], [31, 43, 46, 50], [33, 45, 49, 52]],
      melody: [62, 65, 69, 68, 62],
      density: 0.26, pad: 0.045, tick: 0, pulse: false
    }
  };
  var SCENE_LABELS = {
    menu: '卷宗 · 悬疑', discuss: '讨论 · 暗涌', vote: '投票 · 对峙',
    reveal: '揭晓 · 真相', ending_win: '结局 · 沉冤得雪', ending_lose: '结局 · 迷雾未散'
  };

  // Minecraft 风格：C 大调 / A 小调为主的原创钢琴氛围，不复制任何 Minecraft 原曲
  var MC_SCENES = {
    menu: {
      bpm: 48,
      chords: [[48, 52, 55, 59], [45, 48, 52, 55], [41, 45, 48, 52], [43, 47, 50, 55]],
      melody: [60, 64, 67, 69, 72, 74, 76],
      density: 0.26, pad: 0.028, tick: 0, pulse: false, piano: true, pianoPeak: 0.075, bass: 0.055
    },
    discuss: {
      bpm: 54,
      chords: [[45, 48, 52, 55], [41, 45, 48, 52], [48, 52, 55, 59], [43, 47, 50, 55]],
      melody: [57, 60, 64, 67, 69, 72, 76],
      density: 0.32, pad: 0.024, tick: 0, pulse: false, piano: true, pianoPeak: 0.070, bass: 0.050
    },
    vote: {
      bpm: 58,
      chords: [[38, 41, 45, 48], [34, 38, 41, 45], [41, 45, 48, 52], [43, 47, 50, 55]],
      melody: [62, 65, 69, 72, 74],
      density: 0.22, pad: 0.020, tick: 0, pulse: false, piano: true, pianoPeak: 0.062, bass: 0.045
    },
    reveal: {
      bpm: 46,
      chords: [[41, 45, 48, 52], [43, 47, 50, 54], [40, 43, 47, 50], [45, 48, 52, 55]],
      melody: [60, 64, 67, 71, 72, 76],
      density: 0.24, pad: 0.030, tick: 0, pulse: false, piano: true, pianoPeak: 0.066, bass: 0.050
    },
    ending_win: {
      bpm: 52,
      chords: [[48, 52, 55, 59], [41, 45, 48, 52], [43, 47, 50, 55], [48, 52, 55, 59]],
      melody: [60, 64, 67, 72, 76, 79],
      density: 0.30, pad: 0.030, tick: 0, pulse: false, piano: true, pianoPeak: 0.070, bass: 0.050
    },
    ending_lose: {
      bpm: 44,
      chords: [[45, 48, 52, 55], [41, 45, 48, 52], [38, 41, 45, 48], [40, 43, 47, 50]],
      melody: [57, 60, 62, 64, 67, 69],
      density: 0.20, pad: 0.030, tick: 0, pulse: false, piano: true, pianoPeak: 0.062, bass: 0.050
    }
  };
  var MC_LABELS = {
    menu: '方块 · 晨光', discuss: '方块 · 远行', vote: '方块 · 夜色',
    reveal: '方块 · 回响', ending_win: '方块 · 归乡', ending_lose: '方块 · 余烬'
  };

  var ctx = null;
  var master = null, musicBus = null, sfxBus = null, reverb = null, analyser = null, noiseBuffer = null;
  var started = false;
  var currentScene = 'menu';
  var musicScene = '';
  var musicTimer = null, nextBarTime = 0, barIndex = 0;
  var activeStops = [];
  var playedTokens = {};
  var pendingCues = [];
  var lastCue = '';
  var sfxCount = 0;

  function saveSettings() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(settings)); } catch (e) {}
  }
  function midiToFreq(m) { return 440 * Math.pow(2, (m - 69) / 12); }

  // ---------- 基础音频节点 ----------
  function makeNoise(seconds) {
    var length = Math.max(1, Math.floor(ctx.sampleRate * seconds));
    var buffer = ctx.createBuffer(1, length, ctx.sampleRate);
    var data = buffer.getChannelData(0);
    for (var i = 0; i < length; i++) { data[i] = Math.random() * 2 - 1; }
    return buffer;
  }
  function makeImpulse(seconds, decay) {
    var length = Math.max(1, Math.floor(ctx.sampleRate * seconds));
    var buffer = ctx.createBuffer(2, length, ctx.sampleRate);
    for (var ch = 0; ch < 2; ch++) {
      var data = buffer.getChannelData(ch);
      for (var i = 0; i < length; i++) {
        data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, decay);
      }
    }
    return buffer;
  }
  function ensureContext() {
    if (ctx) { return ctx; }
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) { return null; }
    try { ctx = new AC(); } catch (e) { return null; }
    master = ctx.createGain();
    master.gain.value = settings.volume;
    master.connect(ctx.destination);
    analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    master.connect(analyser);
    musicBus = ctx.createGain();
    musicBus.gain.value = settings.bgm ? 0.55 : 0;
    musicBus.connect(master);
    sfxBus = ctx.createGain();
    sfxBus.gain.value = settings.sfx ? 0.9 : 0;
    sfxBus.connect(master);
    reverb = ctx.createConvolver();
    reverb.buffer = makeImpulse(2.6, 3.0);
    var wet = ctx.createGain();
    wet.gain.value = 0.42;
    reverb.connect(wet);
    wet.connect(master);
    noiseBuffer = makeNoise(2);
    return ctx;
  }
  function connectTo(node, bus, wet) {
    node.connect(bus);
    if (wet && reverb) {
      var send = ctx.createGain();
      send.gain.value = wet;
      node.connect(send);
      send.connect(reverb);
    }
  }

  // 单音：振荡器 + 低通 + 包络；返回可提前收声的 stop 函数（切换场景时用）
  function playTone(bus, freq, t0, dur, opts) {
    opts = opts || {};
    var o = ctx.createOscillator();
    o.type = opts.type || 'sine';
    o.frequency.setValueAtTime(Math.max(20, freq), t0);
    if (opts.slideTo) {
      o.frequency.exponentialRampToValueAtTime(Math.max(20, opts.slideTo), t0 + dur);
    }
    var filter = ctx.createBiquadFilter();
    filter.type = opts.filterType || 'lowpass';
    filter.frequency.setValueAtTime(opts.filterFreq || 2400, t0);
    filter.Q.value = opts.q || 0.7;
    var g = ctx.createGain();
    var peak = opts.peak || 0.1;
    var attack = Math.min(opts.attack || 0.008, dur * 0.5);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), t0 + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(filter);
    filter.connect(g);
    connectTo(g, bus, opts.wet || 0);
    o.start(t0);
    o.stop(t0 + dur + 0.06);
    return function () {
      try {
        g.gain.cancelScheduledValues(ctx.currentTime);
        g.gain.setTargetAtTime(0.0001, ctx.currentTime, 0.05);
        o.stop(ctx.currentTime + 0.15);
      } catch (e) {}
    };
  }

  // 噪声：白噪声 + 滤波扫频 + 包络；用于翻纸、打字、风声、法槌木质感
  function playNoise(bus, t0, dur, opts) {
    opts = opts || {};
    var src = ctx.createBufferSource();
    src.buffer = noiseBuffer;
    src.loop = true;
    var filter = ctx.createBiquadFilter();
    filter.type = opts.filter || 'bandpass';
    filter.frequency.setValueAtTime(Math.max(20, opts.freq || 1200), t0);
    if (opts.sweepTo) {
      filter.frequency.exponentialRampToValueAtTime(Math.max(20, opts.sweepTo), t0 + dur);
    }
    filter.Q.value = opts.q || 0.8;
    var g = ctx.createGain();
    var peak = opts.peak || 0.1;
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), t0 + (opts.attack || 0.008));
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(filter);
    filter.connect(g);
    connectTo(g, bus, opts.wet || 0);
    src.start(t0);
    src.stop(t0 + dur + 0.05);
  }

  // 钟 / 铃：若干非整数倍泛音，衰减较长
  function bell(bus, freq, t0, dur, peak, wet) {
    var partials = [[1, 1], [2.01, 0.42], [2.71, 0.26], [4.08, 0.12]];
    for (var i = 0; i < partials.length; i++) {
      var p = partials[i];
      playTone(bus, freq * p[0], t0, dur * (1 - i * 0.12), {
        type: 'sine', peak: peak * p[1], attack: 0.004, filterFreq: 7000, wet: wet || 0.3
      });
    }
  }

  // 钢琴：基音 + 少量泛音 + 琴槌噪声，短攻击、长衰减；用于 Minecraft 风格 BGM
  function piano(bus, freq, t0, dur, peak, wet) {
    playNoise(bus, t0, 0.028, {
      filter: 'bandpass', freq: 1900, q: 1.8, peak: (peak || 0.05) * 0.16, wet: 0.04
    });
    var partials = [[1, 1], [2, 0.24], [3, 0.10], [4, 0.045], [5, 0.02]];
    for (var i = 0; i < partials.length; i++) {
      var p = partials[i];
      playTone(bus, freq * p[0], t0, dur * (1 - i * 0.08), {
        type: 'sine', peak: (peak || 0.05) * p[1], attack: 0.004,
        filterFreq: 5200 - i * 300, wet: wet || 0.45
      });
    }
  }

  // 铜锣 / 大钟：低频基音 + 金属泛音 + 噪声起音
  function gong(bus, t0, peak) {
    var base = 98;
    var partials = [[1, 1], [1.48, 0.5], [2.09, 0.3], [2.71, 0.18]];
    for (var i = 0; i < partials.length; i++) {
      var p = partials[i];
      playTone(bus, base * p[0], t0, 4.2 - i * 0.4, {
        type: 'sine', peak: (peak || 0.2) * p[1], attack: 0.01, filterFreq: 3000, wet: 0.45
      });
    }
    playNoise(bus, t0, 0.35, { filter: 'lowpass', freq: 420, q: 0.5, peak: (peak || 0.2) * 0.55, wet: 0.2 });
  }

  function heartbeat(bus, t0, peak) {
    playTone(bus, 55, t0, 0.20, { type: 'sine', peak: peak || 0.16, attack: 0.006, slideTo: 38, filterFreq: 240 });
    playTone(bus, 52, t0 + 0.17, 0.24, { type: 'sine', peak: (peak || 0.16) * 0.72, attack: 0.006, slideTo: 34, filterFreq: 220 });
  }

  function woodKnock(bus, t0, peak) {
    playNoise(bus, t0, 0.09, { filter: 'bandpass', freq: 820, q: 7, peak: (peak || 0.2), wet: 0.18 });
    playTone(bus, 120, t0, 0.12, { type: 'sine', peak: (peak || 0.2) * 0.8, slideTo: 70, filterFreq: 400 });
  }

  function duckMusic(t0, amount, seconds) {
    if (!musicBus || !settings.bgm) { return; }
    var base = 0.55;
    try {
      musicBus.gain.cancelScheduledValues(t0);
      musicBus.gain.setValueAtTime(base, t0);
      musicBus.gain.linearRampToValueAtTime(base * amount, t0 + 0.04);
      musicBus.gain.setTargetAtTime(base, t0 + seconds, 0.3);
    } catch (e) {}
  }

  // ---------- 音效库：每个函数接收绝对开始时间 t0（AudioContext 时钟） ----------
  var SFX = {
    click: function (t0) {
      playNoise(sfxBus, t0, 0.035, { filter: 'highpass', freq: 2800, q: 0.7, peak: 0.10 });
      playTone(sfxBus, 1500, t0, 0.04, { type: 'sine', peak: 0.035, filterFreq: 5000 });
    },
    send: function (t0) {
      playNoise(sfxBus, t0, 0.16, { filter: 'bandpass', freq: 900, sweepTo: 2200, q: 1.1, peak: 0.09 });
      playTone(sfxBus, 880, t0 + 0.02, 0.08, { type: 'sine', peak: 0.035 });
    },
    select: function (t0) {
      bell(sfxBus, midiToFreq(76), t0, 0.45, 0.075, 0.25);
      bell(sfxBus, midiToFreq(81), t0 + 0.09, 0.65, 0.065, 0.3);
    },
    role_reveal: function (t0) {
      // 「你的角色卡」翻开：低频上扬 + 两声钟，像把身份牌推到灯下
      playTone(sfxBus, midiToFreq(38), t0, 1.6, {
        type: 'sine', peak: 0.10, attack: 0.25, slideTo: midiToFreq(50), filterFreq: 320, wet: 0.3
      });
      playNoise(sfxBus, t0, 0.8, { filter: 'bandpass', freq: 400, sweepTo: 1600, q: 0.6, peak: 0.06, attack: 0.15 });
      bell(sfxBus, midiToFreq(69), t0 + 0.30, 1.2, 0.085, 0.35);
      bell(sfxBus, midiToFreq(76), t0 + 0.46, 1.4, 0.075, 0.4);
      duckMusic(t0, 0.55, 1.8);
    },
    page: function (t0) {
      playNoise(sfxBus, t0, 0.48, { filter: 'bandpass', freq: 2100, sweepTo: 600, q: 0.7, peak: 0.13, attack: 0.02, wet: 0.12 });
    },
    stamp: function (t0) {
      playTone(sfxBus, 74, t0, 0.32, { type: 'sine', peak: 0.32, attack: 0.004, slideTo: 44, filterFreq: 300 });
      playNoise(sfxBus, t0, 0.12, { filter: 'lowpass', freq: 600, q: 0.5, peak: 0.20, wet: 0.15 });
    },
    seal: function (t0) {
      SFX.stamp(t0);
      bell(sfxBus, midiToFreq(72), t0 + 0.10, 0.8, 0.06, 0.35);
    },
    typewriter: function (t0, params) {
      var count = (params && params.count) || 9;
      for (var i = 0; i < count; i++) {
        var when = t0 + i * 0.085 + Math.random() * 0.025;
        playNoise(sfxBus, when, 0.045, { filter: 'highpass', freq: 3000 + Math.random() * 1800, q: 0.7, peak: 0.055 });
        playTone(sfxBus, 2100 + Math.random() * 900, when, 0.03, { type: 'square', peak: 0.018, filterFreq: 5000 });
      }
    },
    intro: function (t0, params) {
      var titleLen = (params && params.titleLen) || 18;
      var typeDur = Math.min(2.4, Math.max(0.6, titleLen * 0.09));
      SFX.typewriter(t0 + 0.15, { count: Math.min(14, Math.max(5, Math.round(typeDur / 0.09))) });
      playNoise(sfxBus, t0, 0.5, { filter: 'bandpass', freq: 700, sweepTo: 1800, q: 0.6, peak: 0.05, attack: 0.1 });
      SFX.stamp(t0 + typeDur + 0.65);
      duckMusic(t0 + typeDur + 0.65, 0.45, 1.4);
    },
    case_open: function (t0) {
      SFX.page(t0);
      SFX.typewriter(t0 + 0.18, { count: 8 });
      SFX.stamp(t0 + 0.95);
      duckMusic(t0, 0.55, 1.2);
    },
    chime: function (t0) {
      bell(sfxBus, midiToFreq(84), t0, 0.9, 0.07, 0.35);
      bell(sfxBus, midiToFreq(88), t0 + 0.12, 1.0, 0.06, 0.4);
    },
    turn: function (t0) {
      bell(sfxBus, midiToFreq(74), t0, 0.8, 0.11, 0.3);
      bell(sfxBus, midiToFreq(81), t0 + 0.24, 1.1, 0.10, 0.35);
      playTone(sfxBus, midiToFreq(50), t0, 0.9, { type: 'triangle', peak: 0.045, attack: 0.2, filterFreq: 600, wet: 0.25 });
      duckMusic(t0, 0.62, 1.3);
    },
    discuss_start: function (t0) {
      bell(sfxBus, midiToFreq(67), t0, 1.8, 0.10, 0.4);
      bell(sfxBus, midiToFreq(74), t0 + 0.03, 1.6, 0.06, 0.4);
      playNoise(sfxBus, t0, 0.4, { filter: 'bandpass', freq: 1500, sweepTo: 500, q: 0.7, peak: 0.07 });
      duckMusic(t0, 0.55, 1.8);
    },
    vote_start: function (t0) {
      gong(sfxBus, t0, 0.22);
      playNoise(sfxBus, t0 + 0.4, 0.05, { filter: 'highpass', freq: 3200, peak: 0.08 });
      playNoise(sfxBus, t0 + 0.9, 0.05, { filter: 'highpass', freq: 2800, peak: 0.07 });
      duckMusic(t0, 0.36, 2.8);
    },
    tiebreak: function (t0) {
      heartbeat(sfxBus, t0, 0.18);
      heartbeat(sfxBus, t0 + 0.55, 0.15);
      bell(sfxBus, midiToFreq(73), t0 + 0.15, 1.4, 0.07, 0.35);
      bell(sfxBus, midiToFreq(74), t0 + 0.20, 1.4, 0.07, 0.35);
      duckMusic(t0, 0.4, 2.2);
    },
    gavel: function (t0) {
      woodKnock(sfxBus, t0, 0.24);
      woodKnock(sfxBus, t0 + 0.20, 0.26);
    },
    reveal: function (t0) {
      playNoise(sfxBus, t0, 0.9, { filter: 'bandpass', freq: 240, sweepTo: 2400, q: 0.5, peak: 0.10, attack: 0.15 });
      gong(sfxBus, t0 + 0.35, 0.28);
      var chord = [62, 63, 69, 74];
      for (var i = 0; i < chord.length; i++) {
        playTone(sfxBus, midiToFreq(chord[i]), t0 + 0.35, 2.4, {
          type: 'sawtooth', peak: 0.035, attack: 0.08, filterFreq: 1800, wet: 0.4
        });
      }
      duckMusic(t0, 0.30, 3.0);
    },
    win: function (t0) {
      var notes = [62, 66, 69, 74, 78];
      for (var i = 0; i < notes.length; i++) {
        bell(sfxBus, midiToFreq(notes[i]), t0 + i * 0.22, 1.8 - i * 0.15, 0.075, 0.45);
      }
      duckMusic(t0, 0.45, 2.4);
    },
    lose: function (t0) {
      var notes = [62, 57, 53, 50];
      for (var i = 0; i < notes.length; i++) {
        playTone(sfxBus, midiToFreq(notes[i]), t0 + i * 0.38, 2.6, {
          type: 'triangle', peak: 0.075, attack: 0.05, filterFreq: 900, wet: 0.45
        });
      }
      playTone(sfxBus, 38, t0, 4.2, { type: 'sine', peak: 0.10, attack: 0.4, filterFreq: 200, wet: 0.2 });
      duckMusic(t0, 0.45, 2.8);
    }
  };

  // ---------- BGM 生成式循环 ----------
  function stopMusic() {
    if (musicTimer) { clearInterval(musicTimer); musicTimer = null; }
    for (var i = 0; i < activeStops.length; i++) {
      try { activeStops[i].stop(); } catch (e) {}
    }
    activeStops = [];
    musicScene = '';
  }

  function getSceneConfig(sceneName) {
    var table = settings.style === 'minecraft' ? MC_SCENES : SCENES;
    return table[sceneName] || table.menu;
  }

  function scheduleBar(sceneName, index, t0) {
    var scene = getSceneConfig(sceneName);
    var barDur = 60 / scene.bpm * 4;
    var chord = scene.chords[index % scene.chords.length];
    var beat = 60 / scene.bpm;

    // 弦垫：每个和弦音两条微失谐振荡器，慢起慢落
    for (var i = 1; i < chord.length; i++) {
      var freq = midiToFreq(chord[i]);
      var s1 = playTone(musicBus, freq, t0, barDur + 0.7, {
        type: 'triangle', peak: scene.pad, attack: 1.15, filterFreq: scene.piano ? 1100 : 900, wet: 0.30
      });
      var s2 = playTone(musicBus, freq * 1.004, t0, barDur + 0.7, {
        type: 'sine', peak: scene.pad * 0.45, attack: 1.3, filterFreq: 1400, wet: 0.35
      });
      activeStops.push({ stop: s1, until: t0 + barDur + 0.9, obj: s1 });
      activeStops.push({ stop: s2, until: t0 + barDur + 0.9, obj: s2 });
    }
    // 低音根音
    var bassStop = playTone(musicBus, midiToFreq(chord[0]), t0, barDur + 0.5, {
      type: 'sine', peak: scene.bass || 0.085, attack: 0.5, filterFreq: 260, wet: 0.12
    });
    activeStops.push({ stop: bassStop, until: t0 + barDur + 0.7, obj: bassStop });

    // 旋律：Minecraft 风格用稀疏钢琴，悬疑风格用音乐盒
    for (var b = 0; b < 4; b++) {
      if (Math.random() > scene.density) { continue; }
      var note = scene.melody[Math.floor(Math.random() * scene.melody.length)];
      var noteAt = t0 + b * beat + Math.random() * 0.045;
      if (scene.piano) {
        piano(musicBus, midiToFreq(note), noteAt, 2.8, scene.pianoPeak || 0.05, 0.48);
        // 偶尔叠一个低八度，像 Minecraft 里空旷处的回响
        if (Math.random() < 0.24) {
          piano(musicBus, midiToFreq(note - 12), noteAt + 0.03, 3.1, (scene.pianoPeak || 0.05) * 0.42, 0.4);
        }
      } else {
        playTone(musicBus, midiToFreq(note), noteAt, 1.35, {
          type: 'sine', peak: 0.055, attack: 0.006, filterFreq: 5200, wet: 0.42
        });
        playTone(musicBus, midiToFreq(note + 12), noteAt, 0.9, {
          type: 'sine', peak: 0.012, attack: 0.004, filterFreq: 7000, wet: 0.3
        });
      }
    }
    // 轻 tick（讨论阶段像怀表走针）
    if (scene.tick) {
      for (var q = 0; q < 8; q++) {
        playNoise(musicBus, t0 + q * beat / 2, 0.035, {
          filter: 'highpass', freq: 3600, q: 0.8, peak: scene.tick, wet: 0.08
        });
      }
    }
    // 心跳（投票阶段）
    if (scene.pulse) {
      for (var h = 0; h < 4; h++) {
        heartbeat(musicBus, t0 + h * beat, 0.055);
      }
    }
    // 修剪已结束的 stop 函数，避免数组无限增长
    var now = ctx.currentTime;
    activeStops = activeStops.filter(function (item) { return item.until > now; });
  }

  function scheduler() {
    if (!ctx || !settings.bgm || !musicScene) { return; }
    if (nextBarTime < ctx.currentTime - 1) { nextBarTime = ctx.currentTime + 0.12; }
    var guard = 0;
    while (nextBarTime < ctx.currentTime + 1.1 && guard++ < 8) {
      scheduleBar(musicScene, barIndex, nextBarTime);
      var scene = getSceneConfig(musicScene);
      nextBarTime += 60 / scene.bpm * 4;
      barIndex++;
    }
  }

  function startMusic(sceneName) {
    if (!ctx || !settings.bgm) { return; }
    if (musicScene === sceneName && musicTimer) { return; }
    stopMusic();
    musicScene = sceneName;
    barIndex = 0;
    nextBarTime = ctx.currentTime + 0.12;
    musicTimer = setInterval(scheduler, 150);
    scheduler();
  }

  // ---------- 设置 / 面板 ----------
  function refreshUI() {
    var panel = document.getElementById('agent-audio-panel');
    if (!panel) { return; }
    var bgmBtn = panel.querySelector('[data-audio-act="bgm"]');
    var sfxBtn = panel.querySelector('[data-audio-act="sfx"]');
    var styleBtn = panel.querySelector('[data-audio-act="style"]');
    var vol = panel.querySelector('#aap-vol');
    var volNum = panel.querySelector('#aap-vol-num');
    var sceneEl = panel.querySelector('#aap-scene');
    if (bgmBtn) {
      bgmBtn.textContent = settings.bgm ? '♫ 音乐 开' : '♫ 音乐 关';
      bgmBtn.style.color = settings.bgm ? '#C9A227' : '#7d7060';
      bgmBtn.style.borderColor = settings.bgm ? '#6b4f2a' : '#3a3128';
    }
    if (sfxBtn) {
      sfxBtn.textContent = settings.sfx ? '✦ 音效 开' : '✦ 音效 关';
      sfxBtn.style.color = settings.sfx ? '#C9A227' : '#7d7060';
      sfxBtn.style.borderColor = settings.sfx ? '#6b4f2a' : '#3a3128';
    }
    if (styleBtn) {
      styleBtn.textContent = settings.style === 'minecraft' ? '⛏ 音乐风格：Minecraft' : '🎻 音乐风格：悬疑档案';
      styleBtn.style.color = settings.style === 'minecraft' ? '#8fbf6a' : '#C9A227';
      styleBtn.style.borderColor = settings.style === 'minecraft' ? '#4f6b38' : '#6b4f2a';
    }
    if (vol) { vol.value = Math.round(settings.volume * 100); }
    if (volNum) { volNum.textContent = Math.round(settings.volume * 100) + '%'; }
    if (sceneEl) {
      if (!started) { sceneEl.textContent = '点击任意处启用'; }
      else if (!settings.bgm) { sceneEl.textContent = '音乐已关闭'; }
      else {
        var labels = settings.style === 'minecraft' ? MC_LABELS : SCENE_LABELS;
        sceneEl.textContent = labels[currentScene] || (settings.style === 'minecraft' ? '方块 · 氛围' : '悬疑氛围');
      }
    }
  }

  function bindPanel() {
    var panel = document.getElementById('agent-audio-panel');
    if (!panel) { return false; }
    if (panel.__agentAudioBound) { refreshUI(); return true; }
    panel.__agentAudioBound = true;
    var buttons = panel.querySelectorAll('[data-audio-act]');
    for (var i = 0; i < buttons.length; i++) {
      (function (btn) {
        btn.addEventListener('click', function () {
          var action = btn.getAttribute('data-audio-act');
          if (action === 'bgm') { toggleBgm(); }
          else if (action === 'style') { toggleStyle(); }
          else { toggleSfx(); }
        });
      })(buttons[i]);
    }
    var vol = panel.querySelector('#aap-vol');
    if (vol) {
      vol.addEventListener('input', function () { setVolume(parseFloat(vol.value) / 100); });
    }
    refreshUI();
    return true;
  }

  function ensureStarted() {
    var c = ensureContext();
    if (!c) { return; }
    if (c.state === 'suspended') { try { c.resume(); } catch (e) {} }
    if (!started) {
      started = true;
      if (settings.bgm) { startMusic(currentScene || 'menu'); }
      var queued = pendingCues;
      pendingCues = [];
      for (var i = 0; i < queued.length; i++) {
        (function (item) {
          setTimeout(function () {
            if (ctx && SFX[item.cue]) {
              var t0 = ctx.currentTime + 0.05 + (item.delayMs || 0) / 1000;
              SFX[item.cue](t0, item.params || {});
              lastCue = item.cue;
              sfxCount++;
            }
          }, 60 + i * 80);
        })(queued[i]);
      }
      refreshUI();
    }
  }

  function toggleBgm() {
    settings.bgm = !settings.bgm;
    saveSettings();
    if (!ctx) { ensureStarted(); }
    if (settings.bgm) {
      if (!started) { ensureStarted(); }
      else { startMusic(currentScene || 'menu'); }
    } else {
      stopMusic();
    }
    if (musicBus) { musicBus.gain.setTargetAtTime(settings.bgm ? 0.55 : 0, ctx.currentTime, 0.08); }
    refreshUI();
    if (settings.bgm && settings.sfx) { SFX.select(ctx.currentTime + 0.02); }
  }
  function toggleSfx() {
    settings.sfx = !settings.sfx;
    saveSettings();
    if (sfxBus) { sfxBus.gain.setTargetAtTime(settings.sfx ? 0.9 : 0, ctx.currentTime, 0.06); }
    refreshUI();
    if (settings.sfx && ctx) { SFX.click(ctx.currentTime + 0.02); }
  }
  function toggleStyle() {
    settings.style = settings.style === 'minecraft' ? 'suspense' : 'minecraft';
    saveSettings();
    if (ctx && settings.bgm) {
      stopMusic();
      startMusic(currentScene || 'menu');
    }
    refreshUI();
    if (ctx && settings.sfx) { SFX.select(ctx.currentTime + 0.02); }
  }
  function setVolume(v) {
    settings.volume = Math.max(0, Math.min(1, v));
    saveSettings();
    if (master) { master.gain.setTargetAtTime(settings.volume, ctx.currentTime, 0.05); }
    refreshUI();
  }

  function play(cue, token, delayMs, params) {
    if (!cue || !SFX[cue] || !settings.sfx) { return false; }
    if (token) {
      if (playedTokens[token]) { return false; }
      playedTokens[token] = true;
    }
    if (!ctx || !started) {
      pendingCues.push({ cue: cue, delayMs: delayMs || 0, params: params || {} });
      if (pendingCues.length > 8) { pendingCues.shift(); }
      return true;
    }
    var t0 = ctx.currentTime + Math.max(0, delayMs || 0) / 1000;
    if (ctx.state === 'suspended') { try { ctx.resume(); } catch (e) {} }
    SFX[cue](t0, params || {});
    lastCue = cue;
    sfxCount++;
    return true;
  }

  function setScene(sceneName) {
    if (!SCENES[sceneName]) { sceneName = 'menu'; }
    var changed = currentScene !== sceneName;
    currentScene = sceneName;
    if (started && settings.bgm) { startMusic(sceneName); }
    if (changed) { refreshUI(); }
  }

  function getLevel() {
    if (!analyser) { return 0; }
    var data = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(data);
    var sum = 0;
    for (var i = 0; i < data.length; i++) {
      var v = (data[i] - 128) / 128;
      sum += v * v;
    }
    return Math.sqrt(sum / data.length);
  }

  // 全局交互：首次 pointerdown/keydown 解锁 AudioContext；普通按钮补点击音
  if (!window.__agentAudioGlobalBound) {
    window.__agentAudioGlobalBound = true;
    document.addEventListener('pointerdown', function (ev) {
      ensureStarted();
      try {
        var el = ev.target;
        if (el && el.closest && el.closest('#agent-audio-panel')) { return; }
        if (el && el.closest && el.closest('button')) { play('click', '', 0, {}); }
      } catch (e) {}
    }, true);
    document.addEventListener('keydown', function (ev) {
      ensureStarted();
      if (ev.key === 'Enter') {
        var tag = (ev.target && ev.target.tagName) || '';
        if (tag === 'TEXTAREA' || tag === 'INPUT') { play('send', '', 0, {}); }
      }
    }, true);
  }

  window.__agentAudio = {
    play: play,
    setScene: setScene,
    bindPanel: bindPanel,
    refresh: refreshUI,
    toggleBgm: toggleBgm,
    toggleSfx: toggleSfx,
    toggleStyle: toggleStyle,
    setVolume: setVolume,
    getState: function () {
      return {
        ready: !!ctx, started: started, ctxState: ctx ? ctx.state : 'none',
        scene: currentScene, musicScene: musicScene, bgm: settings.bgm, sfx: settings.sfx,
        style: settings.style, volume: settings.volume, lastCue: lastCue, sfxCount: sfxCount, level: getLevel()
      };
    }
  };
  refreshUI();
})();
</script>
"""

_AUDIO_PANEL_HTML = """
<div id="agent-audio-panel" style="font-family:'Microsoft YaHei','PingFang SC',sans-serif; background:linear-gradient(180deg,#1d1916 0%,#171310 100%); border:1px solid #6b4f2a; border-radius:10px; padding:10px 12px; color:#E8DCC8; box-shadow:0 2px 10px rgba(0,0,0,.28);">
  <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:8px;">
    <span style="font-size:12px; color:#C9A227; letter-spacing:2px;">♫ 声音</span>
    <span id="aap-scene" style="font-size:10px; color:#8a7a5f;">点击任意处启用</span>
  </div>
  <div style="display:flex; gap:6px; margin-bottom:9px;">
    <button type="button" data-audio-act="bgm" style="flex:1; background:#221c18; color:#C9A227; border:1px solid #6b4f2a; border-radius:6px; padding:5px 4px; font-size:11px; cursor:pointer; font-family:inherit; transition:all .15s;">♫ 音乐 开</button>
    <button type="button" data-audio-act="sfx" style="flex:1; background:#221c18; color:#C9A227; border:1px solid #6b4f2a; border-radius:6px; padding:5px 4px; font-size:11px; cursor:pointer; font-family:inherit; transition:all .15s;">✦ 音效 开</button>
  </div>
  <button type="button" data-audio-act="style" style="width:100%; background:#1a2016; color:#8fbf6a; border:1px solid #4f6b38; border-radius:6px; padding:5px 4px; margin-bottom:9px; font-size:11px; cursor:pointer; font-family:inherit; transition:all .15s;">⛏ 音乐风格：Minecraft</button>
  <div style="display:flex; align-items:center; gap:8px;">
    <span style="font-size:10px; color:#8a7a5f; white-space:nowrap;">音量</span>
    <input id="aap-vol" type="range" min="0" max="100" step="1" value="55" style="flex:1; height:4px; accent-color:#C9A227; cursor:pointer;">
    <span id="aap-vol-num" style="font-size:10px; color:#8a7a5f; width:30px; text-align:right;">55%</span>
  </div>
</div>
<script>
(function () {
  function tryBind() {
    if (window.__agentAudio) { window.__agentAudio.bindPanel(); return; }
    setTimeout(tryBind, 80);
  }
  tryBind();
})();
</script>
"""


def init_audio_engine() -> None:
    """在页面顶层注入音频引擎（幂等：window.__agentAudio 只初始化一次）。

    这里用固定 HTML 而不是 nonce：Streamlit rerun 时同一位置的 st.html 会复用 DOM，
    引擎对象已挂在 window 上，无需反复执行脚本；整页刷新时脚本会自然重新初始化。
    """
    st.html(_AUDIO_ENGINE_HTML, unsafe_allow_javascript=True)


def render_audio_panel() -> None:
    """渲染音乐/音效控制面板（可放在侧边栏或开始页右栏）。"""
    nonce = time.time_ns()
    st.html(
        f"<!-- audio-panel:{nonce} -->{_AUDIO_PANEL_HTML}",
        unsafe_allow_javascript=True,
    )


def _run_js(body: str) -> None:
    """执行一段音频调用脚本；nonce 保证每次 rerun 都会重新执行。"""
    nonce = time.time_ns()
    st.html(
        f"<script>// audio-call {nonce}\n(function(){{ if (window.__agentAudio) {{ {body} }} }})();</script>",
        unsafe_allow_javascript=True,
    )


def set_audio_scene(scene: str, token: str = "") -> None:
    """切换背景音乐场景。token 仅用于可读性/调试，引擎按场景名幂等切换。"""
    _run_js(f"window.__agentAudio.setScene({json.dumps(scene, ensure_ascii=False)});")


def play_audio_cue(cue: str, token: str = "", delay_ms: int = 0, params: dict | None = None) -> None:
    """播放一次性音效；同一 token 只响一次（页面重复渲染不会重复）。"""
    token_js = json.dumps(token or "", ensure_ascii=False)
    params_js = json.dumps(params or {}, ensure_ascii=False)
    _run_js(
        f"window.__agentAudio.play({json.dumps(cue, ensure_ascii=False)}, "
        f"{token_js}, {int(delay_ms)}, {params_js});"
    )
