"""UI 内嵌 HTML 资源（开场动画、阶段幕布）。

音频（BGM / 音效）统一在 ui_audio.py 中合成与调度；
本模块只保留纯视觉动画，避免多个 iframe 各自创建 AudioContext 导致音效重复/被浏览器拦截。
"""

_INTRO_HTML = """
<div data-tid="__TID__" style="font-family:'KaiTi','楷体',serif; text-align:center; padding:26px 18px; background:#141110; border:1px solid #6b4f2a; border-radius:12px; color:#E8DCC8; min-height:250px; display:flex; flex-direction:column; align-items:center; justify-content:center; position:relative; overflow:hidden;">
  <div id="intro-curtain" style="position:absolute; inset:0; background:#0d0b0a; z-index:3;"></div>
  <div id="intro-body" style="opacity:0; z-index:2;">
    <div style="font-size:12px; color:#8a7a5f; letter-spacing:4px; margin-bottom:14px;">案件卷宗 · 编号 7-0313</div>
    <div id="intro-title" style="font-size:25px; color:#C9A227; letter-spacing:8px; margin:0; font-weight:500; min-height:36px;"></div>
    <div id="intro-sub" style="font-size:12px; color:#a2947a; margin-top:14px; opacity:0; letter-spacing:2px;">真相散落在每个人手里，只有互相盘问才能拼出全貌</div>
  </div>
  <div id="intro-stamp" style="position:absolute; z-index:2; font-size:30px; color:#C0392B; border:3px solid #C0392B; padding:2px 14px; border-radius:6px; transform:rotate(-14deg) scale(2.4); opacity:0; letter-spacing:6px; font-weight:500;">机密</div>
</div>
<script>
(function(){
  var title = __TITLE__;
  var i = 0;
  var titleEl = document.getElementById('intro-title');
  var curtain = document.getElementById('intro-curtain');
  var body = document.getElementById('intro-body');
  var stamp = document.getElementById('intro-stamp');
  var sub = document.getElementById('intro-sub');
  var t = setInterval(function(){
    if (i <= title.length) {
      titleEl.textContent = title.slice(0, i);
      i++;
    } else {
      clearInterval(t);
      stamp.style.transition = 'transform 0.3s ease, opacity 0.15s ease';
      stamp.style.transform = 'rotate(-14deg) scale(1)';
      stamp.style.opacity = '1';
      setTimeout(function(){
        stamp.style.transition = 'opacity 0.5s ease';
        stamp.style.opacity = '0';
        body.style.opacity = '1';
        sub.style.transition = 'opacity 0.8s ease 0.3s';
        sub.style.opacity = '1';
        curtain.style.transition = 'opacity 0.9s ease';
        curtain.style.opacity = '0';
      }, 650);
    }
  }, 90);
})();
</script>
"""

_PHASE_BANNER_HTML = """
<style>html, body { margin: 0; padding: 0; background: transparent; }</style>
<div data-tid="__TID__" style="text-align:center; padding:10px 0; font-family:'KaiTi','楷体',serif;">
  <div id="phase-banner" style="display:inline-block; background:#1d1916; border:1px solid #C9A227; border-radius:10px; padding:10px 30px; color:#C9A227; font-size:18px; letter-spacing:6px; opacity:0;">
    __BANNER__
  </div>
</div>
<style>
@keyframes bannerIn { from { opacity:0; transform:scale(0.85);} to { opacity:1; transform:scale(1);} }
</style>
<script>
(function(){
  var el = document.getElementById('phase-banner');
  el.style.animation = 'bannerIn 0.8s ease forwards';
})();
</script>
"""
