"""启动界面：项目启动时的沉浸式封面（首次进入才展示）。

设计参考（取各自优点，不照搬皮肤）：
- 《疑案追声 / Unheard》：单一顶灯 + 大面积暗部，用一束光把注意力压到画面中心；
- 《Return of the Obra Dinn》：档案 / 卷宗式的边框、编号与打字机气质，先讲故事再给操作；
- 经典悬疑话剧海报：超大衬线标题、夸张字距、金色描边，让标题本身成为主视觉；
- 线下剧本杀物料：火漆印章、红章、绝密标记——把「开始游戏」做成剧情内的「启封卷宗」。

交互：本模块只负责渲染，返回是否点击了「开始游戏」；状态由 app.py 管理。
视觉全部在 st.container(key="launch_screen") 内，通过 fixed 全屏覆盖实现，
不碰游戏页 / 开始页的既有布局。
"""
import streamlit as st

# 尘埃粒子：固定参数（不随机，保证每次启动的构图一致、可复现）
_DUST = "".join(
    f'<i style="left:{left}%;top:{top}%;width:{size}px;height:{size}px;'
    f'animation-duration:{dur}s;animation-delay:-{delay}s;opacity:{op:.2f}"></i>'
    for left, top, size, dur, delay, op in [
        (8, 72, 2, 16, 1, 0.55), (13, 58, 1, 19, 5, 0.40), (18, 81, 3, 22, 8, 0.50),
        (24, 46, 2, 15, 2, 0.45), (29, 67, 1, 21, 11, 0.35), (35, 38, 2, 18, 6, 0.50),
        (41, 76, 1, 24, 3, 0.40), (47, 51, 3, 17, 9, 0.45), (52, 29, 1, 20, 4, 0.30),
        (58, 63, 2, 16, 7, 0.55), (63, 44, 1, 23, 12, 0.35), (68, 78, 2, 19, 2, 0.50),
        (73, 35, 1, 18, 10, 0.30), (78, 59, 3, 21, 5, 0.45), (83, 70, 1, 17, 8, 0.40),
        (88, 48, 2, 22, 1, 0.50), (92, 66, 1, 20, 6, 0.35), (95, 39, 2, 18, 13, 0.30),
    ]
)

_LAUNCH_STYLE = """
<style>
/* ==================== 启动封面 · 全屏舞台 ==================== */
.st-key-launch_screen {
  position: fixed !important;
  inset: 0 !important;
  width: 100vw !important;
  max-width: 100vw !important;
  height: 100vh !important;
  height: 100dvh !important;
  min-height: 100vh !important;
  min-height: 100dvh !important;
  margin: 0 !important;
  padding: 0 !important;
  gap: 0 !important;
  align-items: center !important;
  justify-content: center !important;
  overflow: hidden !important;
  background: #080605 !important;
  z-index: 99999 !important;
}
.st-key-launch_screen > [data-testid="stElementContainer"] {
  width: 100% !important;
  max-width: 100% !important;
  display: flex;
  justify-content: center;
}
.st-key-launch_screen [data-testid="stMarkdownContainer"] { width: 100%; }
.st-key-launch_screen [data-testid="stMarkdownContainer"] p { margin: 0; }

/* 场景底板 */
.launch-scene {
  position: fixed;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
  background:
    radial-gradient(ellipse 90% 65% at 50% 34%, #241910 0%, #17110d 42%, #0b0807 74%, #060504 100%);
  animation: sceneIn 1.1s ease both;
}
.launch-scene:before {
  content: "";
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse 50% 55% at 4% 12%, rgba(52, 80, 98, .20), transparent 62%),
    radial-gradient(ellipse 46% 50% at 96% 18%, rgba(46, 68, 86, .16), transparent 65%);
}
.launch-scene:after {
  content: "";
  position: absolute;
  inset: 0;
  background: repeating-linear-gradient(90deg, transparent 0 119px, rgba(230, 210, 180, .012) 119px 120px);
}

/* 右侧拱窗：夜雨 + 月光 */
.scene-window {
  position: absolute;
  top: 10.5vh;
  right: 6.5vw;
  width: min(23vw, 330px);
  height: min(60vh, 570px);
  opacity: .62;
  filter: blur(.25px);
  animation: windowIn 2.4s ease 0.3s both;
}
.win-glass {
  position: absolute;
  inset: 0;
  overflow: hidden;
  border: 10px solid #100c09;
  border-bottom-width: 14px;
  border-radius: 999px 999px 0 0;
  background:
    radial-gradient(circle at 62% 22%, rgba(214, 229, 240, .25) 0 2px, rgba(150, 180, 205, .10) 14%, transparent 34%),
    linear-gradient(180deg, #1c2a34 0%, #101a22 48%, #090d11 100%);
  box-shadow:
    0 0 0 2px rgba(201, 162, 39, .10),
    0 0 70px rgba(92, 128, 155, .10),
    inset 0 0 90px rgba(0, 0, 0, .82);
}
.win-moon {
  position: absolute;
  top: 14%;
  left: 58%;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: radial-gradient(circle at 38% 35%, #eef4f2, #b9cbd2 55%, #8aa0ad 100%);
  box-shadow: 0 0 24px 8px rgba(206, 226, 235, .30), 0 0 90px 32px rgba(150, 190, 210, .12);
  opacity: .75;
}
.win-rain {
  position: absolute;
  inset: -25%;
  background-image: repeating-linear-gradient(103deg, transparent 0 25px, rgba(202, 224, 240, .13) 25px 26px, transparent 26px 58px);
  opacity: .55;
}
.win-rain.r1 { animation: winRain 1.15s linear infinite; }
.win-rain.r2 {
  background-image: repeating-linear-gradient(100deg, transparent 0 34px, rgba(202, 224, 240, .08) 34px 35px, transparent 35px 77px);
  animation: winRain2 1.7s linear infinite;
  opacity: .45;
}
.win-muntin-v, .win-muntin-h { position: absolute; background: #0f0b08; box-shadow: 0 0 8px rgba(0,0,0,.9); }
.win-muntin-v { top: 0; bottom: 0; left: 50%; width: 9px; transform: translateX(-50%); }
.win-muntin-h { left: 0; right: 0; top: 44%; height: 9px; }

/* 室内全景雨丝（更细，作为前景空气感） */
.scene-rain {
  position: absolute;
  inset: -20% -12%;
  pointer-events: none;
  background-image: repeating-linear-gradient(104deg, transparent 0 31px, rgba(190, 215, 235, .045) 31px 32px, transparent 32px 73px);
  opacity: .55;
  -webkit-mask-image: linear-gradient(180deg, transparent 0, #000 14%, #000 82%, transparent 100%);
  mask-image: linear-gradient(180deg, transparent 0, #000 14%, #000 82%, transparent 100%);
}
.scene-rain.r1 { animation: hallRain 1.35s linear infinite; }
.scene-rain.r2 {
  background-image: repeating-linear-gradient(99deg, transparent 0 47px, rgba(190, 215, 235, .028) 47px 48px, transparent 48px 103px);
  animation: hallRain2 2.1s linear infinite;
}

/* 雾气：慢速漂移，制造体积感 */
.scene-fog {
  position: absolute;
  border-radius: 50%;
  filter: blur(70px);
  mix-blend-mode: screen;
  pointer-events: none;
}
.fog-a {
  width: 52vw; height: 30vh; left: -12vw; bottom: 4vh;
  background: radial-gradient(closest-side, rgba(130, 145, 150, .10), transparent 72%);
  animation: fogDriftA 27s ease-in-out infinite alternate;
}
.fog-b {
  width: 46vw; height: 26vh; right: -8vw; bottom: 10vh;
  background: radial-gradient(closest-side, rgba(110, 135, 150, .09), transparent 70%);
  animation: fogDriftB 33s ease-in-out infinite alternate;
}
.fog-c {
  width: 40vw; height: 20vh; left: 28vw; bottom: -6vh;
  background: radial-gradient(closest-side, rgba(180, 160, 130, .08), transparent 70%);
  animation: fogDriftC 22s ease-in-out infinite alternate;
}

/* 吊灯：唯一暖光源 */
.scene-lamp {
  position: absolute;
  top: 0;
  left: 50%;
  width: 300px;
  height: 340px;
  transform: translateX(-50%);
  pointer-events: none;
  z-index: 1;
  animation: lampOn 1.6s ease 0.15s both;
}
.lamp-cord {
  position: absolute;
  top: -24px;
  left: 50%;
  width: 2px;
  height: 78px;
  background: linear-gradient(180deg, #030202, #2e2419);
  transform: translateX(-50%);
}
.lamp-shade {
  position: absolute;
  top: 54px;
  left: 50%;
  width: 176px;
  height: 64px;
  transform: translateX(-50%);
  background: linear-gradient(180deg, #211a13 0%, #0d0a07 62%, #060504 100%);
  clip-path: polygon(31% 0, 69% 0, 100% 100%, 0 100%);
  box-shadow: 0 16px 32px rgba(0, 0, 0, .75);
}
.lamp-shade:after {
  content: "";
  position: absolute;
  left: 0; right: 0; bottom: 0;
  height: 3px;
  background: linear-gradient(90deg, transparent, rgba(201, 162, 39, .38), transparent);
}
.lamp-bulb {
  position: absolute;
  top: 112px;
  left: 50%;
  width: 24px;
  height: 13px;
  transform: translateX(-50%);
  border-radius: 0 0 50% 50%;
  background: radial-gradient(ellipse at 50% 35%, #fff6d8 0%, #ffdc94 45%, #d9a84a 100%);
  box-shadow:
    0 0 16px 6px rgba(255, 220, 145, .70),
    0 0 70px 30px rgba(255, 195, 105, .16);
  animation: bulbFlicker 7.5s ease-in-out infinite;
}
.lamp-cone {
  position: absolute;
  top: 118px;
  left: 50%;
  width: 128vw;
  height: 76vh;
  transform: translateX(-50%);
  background: linear-gradient(180deg, rgba(255, 219, 158, .115) 0%, rgba(255, 205, 130, .045) 46%, transparent 79%);
  clip-path: polygon(46.8% 0, 53.2% 0, 100% 100%, 0 100%);
  filter: blur(18px);
  mix-blend-mode: screen;
}
.lamp-cone.soft {
  top: 96px;
  height: 92vh;
  background: linear-gradient(180deg, rgba(255, 219, 158, .06) 0%, rgba(255, 205, 130, .025) 50%, transparent 82%);
  filter: blur(38px);
  opacity: .8;
}

/* 尘埃 */
.scene-dust { position: absolute; inset: 0; overflow: hidden; }
.scene-dust i {
  position: absolute;
  border-radius: 50%;
  background: rgba(255, 230, 180, .65);
  box-shadow: 0 0 6px rgba(255, 220, 160, .40);
  animation-name: dustFloat;
  animation-timing-function: linear;
  animation-iteration-count: infinite;
}

/* 远处闪电：很轻，只做天光变化 */
.scene-lightning {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(ellipse 40% 50% at 18% 0%, rgba(175, 205, 235, .16), transparent 60%);
  opacity: 0;
  animation: lightning 17s ease-in-out infinite;
}
.scene-vignette {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(ellipse 62% 54% at 50% 40%, transparent 0%, rgba(4, 3, 2, .32) 62%, rgba(3, 2, 2, .88) 100%);
}
.scene-grain {
  position: absolute;
  inset: -40%;
  pointer-events: none;
  opacity: .055;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.82' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.6'/%3E%3C/svg%3E");
  animation: grainShift 1.4s steps(3) infinite;
}

/* 画面外框：档案封套的双线 + 四角 */
.launch-frame {
  position: fixed;
  inset: 22px;
  border: 1px solid rgba(201, 162, 39, .17);
  pointer-events: none;
  z-index: 3;
  animation: frameIn 1.8s ease 0.3s both;
}
.launch-frame:after {
  content: "";
  position: absolute;
  inset: 6px;
  border: 1px solid rgba(201, 162, 39, .07);
}
.launch-frame .fc { position: absolute; width: 24px; height: 24px; border-color: rgba(201, 162, 39, .55); border-style: solid; }
.launch-frame .tl { top: -1px; left: -1px; border-width: 1px 0 0 1px; }
.launch-frame .tr { top: -1px; right: -1px; border-width: 1px 1px 0 0; }
.launch-frame .bl { bottom: -1px; left: -1px; border-width: 0 0 1px 1px; }
.launch-frame .br { bottom: -1px; right: -1px; border-width: 0 1px 1px 0; }

/* 顶部 / 侧边 / 底部档案小字 */
.launch-topmeta {
  position: fixed;
  top: 40px;
  left: 54px;
  right: 54px;
  display: flex;
  justify-content: space-between;
  z-index: 4;
  pointer-events: none;
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  font-size: 10px;
  letter-spacing: 3px;
  color: rgba(201, 180, 140, .42);
  animation: fadeIn 1.4s ease 0.8s both;
}
.launch-sidemark {
  position: fixed;
  top: 50%;
  z-index: 4;
  pointer-events: none;
  font-family: 'Georgia', 'Times New Roman', serif;
  font-size: 9px;
  letter-spacing: 6px;
  color: rgba(201, 180, 140, .22);
  writing-mode: vertical-rl;
  animation: fadeIn 2s ease 1.1s both;
}
.launch-sidemark.left { left: 34px; transform: translateY(-50%) rotate(180deg); }
.launch-sidemark.right { right: 34px; transform: translateY(-50%); }
.launch-footer {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 40px;
  z-index: 4;
  pointer-events: none;
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 14px;
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  font-size: 9px;
  letter-spacing: 3px;
  color: rgba(201, 180, 140, .34);
  animation: fadeIn 1.6s ease 1.9s both;
}
.launch-footer i { width: 21px; height: 1px; background: rgba(201, 162, 39, .22); }

/* ==================== 中央标题与文案 ==================== */
.launch-content {
  position: relative;
  z-index: 5;
  text-align: center;
  padding: 0 7vw;
  pointer-events: none;
}
.launch-eyebrow {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  margin-bottom: 18px;
  opacity: 0;
  animation: riseIn 1s cubic-bezier(.2, .8, .2, 1) 0.5s forwards;
}
.launch-eyebrow .line {
  width: clamp(30px, 6vw, 92px);
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(201, 162, 39, .45));
}
.launch-eyebrow .line:last-child { transform: scaleX(-1); }
.launch-eyebrow .txt {
  font-family: 'Georgia', 'Times New Roman', serif;
  font-size: clamp(8px, .78vw, 11px);
  letter-spacing: .38em;
  color: rgba(201, 175, 120, .78);
  white-space: nowrap;
}
.launch-title {
  font-family: 'STKaiti', 'KaiTi', '楷体', 'STSong', 'Songti SC', 'Noto Serif CJK SC', serif;
  font-size: clamp(44px, 6.3vw, 148px);
  line-height: 1.1;
  font-weight: 600;
  letter-spacing: .12em;
  padding-left: .12em;
  white-space: nowrap;
  background: linear-gradient(180deg, #fff1d2 0%, #efd79d 30%, #d3ab55 55%, #a5782c 78%, #80581c 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  filter: drop-shadow(0 3px 1px rgba(0, 0, 0, .70)) drop-shadow(0 0 34px rgba(201, 162, 39, .22));
  opacity: 0;
  animation: titleIn 1.3s cubic-bezier(.18, .85, .25, 1) 0.6s forwards;
}
.launch-title .t-dot { padding: 0 .16em; }
.launch-ornament {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin: 14px 0 18px;
  opacity: 0;
  animation: riseIn .9s ease 1.15s forwards;
}
.launch-ornament span {
  width: clamp(52px, 8vw, 118px);
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(201, 162, 39, .52));
}
.launch-ornament span:last-child { transform: scaleX(-1); }
.launch-ornament b {
  font-size: 7px;
  color: rgba(201, 162, 39, .85);
  font-weight: 400;
  letter-spacing: 2px;
}
.launch-tagline {
  font-family: 'STKaiti', 'KaiTi', '楷体', 'STSong', serif;
  font-size: clamp(17px, 1.3vw, 28px);
  letter-spacing: .22em;
  color: #d6c7a6 !important;
  text-shadow: 0 2px 10px rgba(0, 0, 0, .8);
  opacity: 0;
  animation: riseIn .95s cubic-bezier(.2, .8, .2, 1) 1.25s forwards;
}
.launch-subline {
  margin-top: 10px !important;
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  font-size: clamp(10px, .85vw, 15px);
  letter-spacing: .24em;
  color: rgba(190, 172, 137, .64) !important;
  opacity: 0;
  animation: riseIn .95s cubic-bezier(.2, .8, .2, 1) 1.42s forwards;
}
.launch-stats {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 13px;
  margin-top: 18px;
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  font-size: clamp(10px, .75vw, 12px);
  letter-spacing: .16em;
  color: rgba(180, 161, 126, .60);
  opacity: 0;
  animation: riseIn .9s ease 1.58s forwards;
}
.launch-stats b { color: rgba(219, 187, 116, .92); font-weight: 500; }
.launch-stats i { width: 3px; height: 3px; border-radius: 50%; background: rgba(201, 162, 39, .40); }

/* ==================== 火漆印章按钮 ==================== */
.st-key-launch_start {
  width: 100% !important;
  display: flex !important;
  justify-content: center !important;
  margin-top: 26px !important;
  padding: 0 !important;
}
.st-key-launch_start > div { width: auto !important; }
.st-key-launch_start .stButton {
  position: relative;
  width: fit-content;
  height: fit-content;
  padding: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.st-key-launch_start .stButton:before,
.st-key-launch_start .stButton:after {
  content: "";
  position: absolute;
  inset: -7px;
  border: 1px solid rgba(201, 162, 39, .28);
  border-radius: 50%;
  pointer-events: none;
  opacity: 0;
  animation: sealPulse 3.4s ease-out 2.2s infinite;
}
.st-key-launch_start .stButton:after {
  inset: -19px;
  border-color: rgba(201, 162, 39, .14);
  animation-delay: 2.8s;
}
.st-key-launch_start button {
  width: clamp(164px, 11.2vw, 196px) !important;
  height: clamp(164px, 11.2vw, 196px) !important;
  min-height: clamp(164px, 11.2vw, 196px) !important;
  padding: 0 !important;
  border-radius: 49% 51% 48% 52% / 51% 49% 52% 48% !important;
  border: 1px solid rgba(255, 225, 180, .48) !important;
  background:
    radial-gradient(circle at 34% 27%, #e25d47 0%, #bd3326 27%, #931f17 55%, #66120d 82%, #4b0b08 100%) !important;
  color: #FFE9C9 !important;
  display: flex !important;
  flex-direction: column !important;
  align-items: center !important;
  justify-content: center !important;
  font-family: 'STKaiti', 'KaiTi', '楷体', serif !important;
  font-size: clamp(21px, 1.35vw, 25px) !important;
  letter-spacing: 5px !important;
  box-shadow:
    0 0 0 6px rgba(201, 162, 39, .10),
    0 0 0 7px rgba(201, 162, 39, .32),
    inset 0 0 0 7px rgba(255, 222, 180, .07),
    inset 0 0 0 8px rgba(0, 0, 0, .22),
    0 16px 40px rgba(0, 0, 0, .72),
    0 0 48px rgba(192, 57, 43, .26),
    inset 0 2px 6px rgba(255, 255, 255, .16),
    inset 0 -12px 26px rgba(0, 0, 0, .45) !important;
  transform: rotate(-4deg);
  transition: transform .38s cubic-bezier(.2, .8, .2, 1), box-shadow .38s ease, filter .38s ease !important;
  animation: sealStamp .75s cubic-bezier(.16, 1.3, .3, 1) 1.75s both;
}
.st-key-launch_start button:hover {
  transform: rotate(0deg) scale(1.035) !important;
  filter: brightness(1.08);
  box-shadow:
    0 0 0 7px rgba(201, 162, 39, .16),
    0 0 0 8px rgba(201, 162, 39, .45),
    0 20px 46px rgba(0, 0, 0, .78),
    0 0 66px rgba(192, 57, 43, .40),
    inset 0 2px 6px rgba(255, 255, 255, .20),
    inset 0 -12px 26px rgba(0, 0, 0, .42) !important;
}
.st-key-launch_start button:active { transform: rotate(0deg) scale(.975) !important; }
.st-key-launch_start button p {
  font-family: inherit !important;
  font-size: inherit !important;
  letter-spacing: inherit !important;
  line-height: 1.1 !important;
  margin: 0 !important;
  white-space: nowrap;
}
.st-key-launch_start button:after {
  content: "ENTER";
  display: block;
  margin-top: 8px;
  font-family: 'Georgia', serif;
  font-size: 8px;
  letter-spacing: 4px;
  color: rgba(255, 233, 201, .58);
}

/* 按钮下方提示 */
.launch-tail {
  position: relative;
  z-index: 5;
  text-align: center;
  margin-top: 8px;
  pointer-events: none;
}
.launch-hint {
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  font-size: 10px;
  letter-spacing: .34em;
  color: rgba(201, 180, 140, .65);
  opacity: 0;
  animation: hintIn 1s ease 2.35s both;
}
.launch-hint:before,
.launch-hint:after {
  content: "";
  display: inline-block;
  vertical-align: middle;
  width: 26px;
  height: 1px;
  margin: 0 12px;
  background: rgba(201, 162, 39, .28);
}
.launch-note {
  margin-top: 12px !important;
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  font-size: 9px;
  letter-spacing: .22em;
  color: rgba(150, 135, 110, .28) !important;
  opacity: 0;
  animation: fadeIn 1.2s ease 2.7s both;
}

/* ==================== 动效 ==================== */
@keyframes sceneIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes frameIn { from { opacity: 0; transform: scale(1.015); } to { opacity: 1; transform: scale(1); } }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes windowIn { from { opacity: 0; filter: blur(6px); } to { opacity: .62; filter: blur(.25px); } }
@keyframes lampOn {
  0% { opacity: 0; }
  18% { opacity: 1; }
  26% { opacity: .35; }
  33% { opacity: 1; }
  41% { opacity: .55; }
  100% { opacity: 1; }
}
@keyframes bulbFlicker {
  0%, 100% { opacity: 1; }
  43% { opacity: 1; }
  44% { opacity: .72; }
  45% { opacity: 1; }
  71% { opacity: .85; }
  72% { opacity: 1; }
}
@keyframes riseIn {
  from { opacity: 0; transform: translateY(16px); filter: blur(5px); }
  to { opacity: 1; transform: translateY(0); filter: blur(0); }
}
@keyframes titleIn {
  0% { opacity: 0; letter-spacing: .30em; filter: blur(13px); transform: scale(.985); }
  55% { opacity: 1; }
  100% { opacity: 1; letter-spacing: .12em; filter: blur(0); transform: scale(1); }
}
@keyframes hintIn {
  from { opacity: 0; letter-spacing: .55em; }
  to { opacity: 1; letter-spacing: .34em; }
}
@keyframes sealStamp {
  0% { opacity: 0; transform: rotate(-18deg) scale(2.15); }
  55% { opacity: 1; transform: rotate(-5deg) scale(.94); }
  78% { transform: rotate(-4deg) scale(1.025); }
  100% { opacity: 1; transform: rotate(-4deg) scale(1); }
}
@keyframes sealPulse {
  0% { transform: scale(.92); opacity: .75; }
  60% { transform: scale(1.10); opacity: 0; }
  100% { transform: scale(1.10); opacity: 0; }
}
@keyframes winRain {
  from { background-position: 0 0; }
  to { background-position: -80px 520px; }
}
@keyframes winRain2 {
  from { background-position: 0 0; }
  to { background-position: -46px 460px; }
}
@keyframes hallRain {
  from { background-position: 0 0; }
  to { background-position: -120px 640px; }
}
@keyframes hallRain2 {
  from { background-position: 0 0; }
  to { background-position: -70px 520px; }
}
@keyframes fogDriftA { from { transform: translate3d(0, 0, 0) scale(1); } to { transform: translate3d(9vw, -3vh, 0) scale(1.12); } }
@keyframes fogDriftB { from { transform: translate3d(0, 0, 0) scale(1.05); } to { transform: translate3d(-8vw, 2vh, 0) scale(1); } }
@keyframes fogDriftC { from { transform: translate3d(0, 0, 0); } to { transform: translate3d(6vw, -2vh, 0); } }
@keyframes dustFloat {
  0% { transform: translate3d(0, 34px, 0); opacity: 0; }
  14% { opacity: .65; }
  72% { opacity: .35; }
  100% { transform: translate3d(26px, -110px, 0); opacity: 0; }
}
@keyframes lightning {
  0%, 8.2%, 9.0%, 9.8%, 100% { opacity: 0; }
  8.45% { opacity: .42; }
  8.65% { opacity: .04; }
  9.15% { opacity: .22; }
  9.4% { opacity: 0; }
}
@keyframes grainShift {
  0% { transform: translate(0, 0); }
  33% { transform: translate(-1.5%, 1%); }
  66% { transform: translate(1%, -1.2%); }
  100% { transform: translate(0, 0); }
}

/* 进入开始页时的柔和过渡 */
.st-key-start_page { animation: startPageIn .75s cubic-bezier(.2, .8, .2, 1) both; }
@keyframes startPageIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }

/* 矮屏 / 窄屏 */
@media (max-height: 760px) {
  .launch-title { font-size: clamp(38px, 5.2vw, 64px); }
  .launch-ornament { margin: 9px 0 12px; }
  .launch-tagline { font-size: 17px; }
  .launch-stats { margin-top: 12px; }
  .st-key-launch_start { margin-top: 16px !important; }
  .st-key-launch_start button { width: 142px !important; height: 142px !important; min-height: 142px !important; font-size: 18px !important; }
  .launch-footer { bottom: 26px; }
  .launch-topmeta { top: 32px; }
}
@media (max-width: 900px) {
  .scene-window { display: none; }
  .launch-sidemark { display: none; }
  .launch-title { font-size: clamp(36px, 8.4vw, 62px); letter-spacing: .08em; padding-left: .08em; }
  .launch-content { padding: 0 5vw; }
  .launch-topmeta { left: 32px; right: 32px; letter-spacing: 2px; }
  .launch-frame { inset: 14px; }
}
@media (max-width: 520px) {
  .launch-title { font-size: 40px; line-height: 1.24; white-space: normal; letter-spacing: .16em; padding-left: 0; }
  .launch-title .t1, .launch-title .t2 { display: block; }
  .launch-title .t-dot { display: none; }
  .launch-eyebrow .txt { font-size: 7px; letter-spacing: .22em; }
  .launch-tagline { font-size: 15px; letter-spacing: .12em; }
  .launch-subline { font-size: 9px; letter-spacing: .08em; line-height: 1.8; }
  .launch-stats { gap: 8px; font-size: 9px; letter-spacing: .08em; flex-wrap: wrap; row-gap: 5px; }
  .launch-topmeta span:last-child { display: none; }
  .launch-footer { gap: 8px; font-size: 8px; letter-spacing: 1px; }
  .launch-hint { font-size: 9px; letter-spacing: .2em; }
  .launch-hint:before, .launch-hint:after { width: 14px; margin: 0 7px; }
}
@media (prefers-reduced-motion: reduce) {
  .st-key-launch_screen *, .st-key-launch_screen *:before, .st-key-launch_screen *:after {
    animation-duration: .01ms !important;
    animation-delay: 0ms !important;
    transition-duration: .01ms !important;
  }
}
</style>
"""

_LAUNCH_SCENE_HTML = """
<div class="launch-scene" aria-hidden="true">
  <div class="scene-window">
    <div class="win-glass">
      <span class="win-moon"></span>
      <span class="win-rain r1"></span>
      <span class="win-rain r2"></span>
      <span class="win-muntin-v"></span>
      <span class="win-muntin-h"></span>
    </div>
  </div>
  <div class="scene-fog fog-a"></div>
  <div class="scene-fog fog-b"></div>
  <div class="scene-fog fog-c"></div>
  <div class="scene-rain r1"></div>
  <div class="scene-rain r2"></div>
  <div class="scene-lamp">
    <span class="lamp-cord"></span>
    <span class="lamp-shade"></span>
    <span class="lamp-bulb"></span>
    <span class="lamp-cone"></span>
    <span class="lamp-cone soft"></span>
  </div>
  <div class="scene-dust">__DUST__</div>
  <div class="scene-lightning"></div>
  <div class="scene-vignette"></div>
  <div class="scene-grain"></div>
</div>
<div class="launch-frame" aria-hidden="true">
  <i class="fc tl"></i><i class="fc tr"></i><i class="fc bl"></i><i class="fc br"></i>
</div>
<div class="launch-topmeta" aria-hidden="true">
  <span>CASE FILE · No. 07-0313</span>
  <span>档案等级 · 绝密</span>
</div>
<div class="launch-sidemark left" aria-hidden="true">MURDER MYSTERY</div>
<div class="launch-sidemark right" aria-hidden="true">MIDNIGHT · 00:00</div>
<div class="launch-footer" aria-hidden="true">
  <span>建议佩戴耳机</span><i></i><span>AI HOSTED</span><i></i><span>推理之夜 · 2026</span>
</div>
""".replace("__DUST__", _DUST)

_LAUNCH_HEAD_HTML = """
<div class="launch-content">
  <div class="launch-eyebrow">
    <span class="line"></span>
    <span class="txt">MURDER MYSTERY · MIDNIGHT ARCHIVE</span>
    <span class="line"></span>
  </div>
  <div class="launch-title"><span class="t1">剧本杀</span><span class="t-dot">·</span><span class="t2">推理之夜</span></div>
  <div class="launch-ornament"><span></span><b>◆</b><span></span></div>
  <p class="launch-tagline">今夜，人人都有秘密。</p>
  <p class="launch-subline">一桩命案，六位嫌疑人，真凶就在你们之中。</p>
  <div class="launch-stats">
    <span><b>4–6</b> 名嫌疑人</span><i></i>
    <span><b>15–30</b> 分钟一局</span><i></i>
    <span><b>AI</b> 实时主持</span>
  </div>
</div>
"""

_LAUNCH_TAIL_HTML = """
<div class="launch-tail">
  <div class="launch-hint">点击封印 · 启封卷宗</div>
  <p class="launch-note">本局剧本由 AI 即时生成 · 请保持好奇心</p>
</div>
"""


def render_launch_screen() -> bool:
    """渲染启动封面，返回用户是否点击了「开始游戏」。

    布局全部收在 key="launch_screen" 的容器里：
    - 场景层 fixed 铺满视口，盖住 Streamlit 主区域与顶栏；
    - 中央内容按 flex 垂直居中；
    - 按钮是原生 st.button，点击触发一次 rerun，由 app.py 切换状态。
    """
    with st.container(key="launch_screen"):
        st.markdown(_LAUNCH_STYLE, unsafe_allow_html=True)
        st.markdown(_LAUNCH_SCENE_HTML, unsafe_allow_html=True)
        st.markdown(_LAUNCH_HEAD_HTML, unsafe_allow_html=True)
        clicked = st.button("开 始 游 戏", key="launch_start")
        st.markdown(_LAUNCH_TAIL_HTML, unsafe_allow_html=True)
    return clicked
