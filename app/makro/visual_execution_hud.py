"""Visual execution HUD for the real Makro browser tab.

The visual language is ported from the existing mobile visual-agent HUD in
``yuchenm1303-png/yuchen1303.github.io`` (``dev-update-1``), but the desktop
browser renderer keeps text at native CSS pixel size.  The HUD never steers
Playwright; it only mirrors the real DOM operations performed by the existing
executor.
"""

from __future__ import annotations

from typing import Any

HUD_FRAME_ID = "listing-studio-visual-agent-hud"
HUD_API_KEY = "__listingStudioVisualHud"

# Desktop HUD: the bubble keeps the mobile visual language without scaling the
# whole text surface.  Native-size glyphs avoid Chromium/Windows re-rasterizing
# 12-13px Chinese text down to ~8px, which previously looked like mojibake.
_HUD_SRCDOC = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<style>
:root{
  --text:#f4f8fb;--muted:#9fb0c1;--accent:#66d9ff;--success:#6ce6b5;
  --cursor-x:50vw;--cursor-y:50vh;--cursor-move-speed:210ms;
  --bubble-x:calc(50vw + 34px);--bubble-y:calc(50vh + 18px);
  --lab-cursor-size:36.1px;--lab-scale-x:1;--lab-scale-y:.95;
  --lab-rotation:-2.5deg;--lab-offset-x:-.5px;--lab-offset-y:-.5px;
  --lab-aura-size:72px;--lab-aura-blur:8px;--lab-aura-opacity:.54;
  --hotspot-x:10px;--hotspot-y:10.5px;
  --info-bubble-width:292px;
  --edge-inset:0px;--edge-radius:0px;--edge-halo-width:0px;
  --edge-halo-blur:0px;--edge-halo-opacity:.58;--edge-cast-depth:29.2px;
  --edge-cast-blur:0px;--edge-cast-opacity:.8;--edge-flow-speed:7.5s;
  --edge-breath-speed:1.5s;--edge-breath-strength:.55;
}
*{box-sizing:border-box}
html,body{margin:0;width:100%;height:100%;min-height:0;background:transparent;color:var(--text);font-family:"Segoe UI","Microsoft YaHei UI","Microsoft YaHei","PingFang SC",Arial,sans-serif;overflow:hidden;pointer-events:none;text-rendering:optimizeLegibility;-webkit-font-smoothing:antialiased}
.app{position:relative;width:100%;height:100%;overflow:hidden;background:transparent}
.hud{position:absolute;inset:0;z-index:20;pointer-events:none;overflow:hidden;opacity:1;visibility:visible;transition:opacity .22s ease,visibility .22s ease}
.app.hud-hidden .hud{opacity:0;visibility:hidden}
@property --edge-angle{syntax:"<angle>";inherits:false;initial-value:0deg}
.edge-aurora{position:absolute;inset:0;pointer-events:none;overflow:hidden;contain:layout paint style;isolation:isolate}
.edge-aurora-layer{position:absolute;inset:var(--edge-inset);border-radius:var(--edge-radius);pointer-events:none;background:conic-gradient(from var(--edge-angle) at 50% 50%,#62f3ff 0deg,#58c7ff 34deg,#7779ff 72deg,#ba62ff 112deg,#ff4ab8 156deg,#ff4979 198deg,#ff9e4d 234deg,#ffe55a 268deg,#abf15f 302deg,#48eacb 336deg,#62f3ff 360deg);animation:edgeColorOrbit var(--edge-flow-speed) linear infinite,edgeLayerBreath var(--edge-breath-speed) ease-in-out infinite;will-change:opacity,filter}
.edge-aurora-cast{--layer-opacity:var(--edge-cast-opacity);filter:blur(var(--edge-cast-blur)) saturate(1.16);-webkit-mask:linear-gradient(to bottom,#000 0%,rgba(0,0,0,.82) 18%,rgba(0,0,0,.34) 55%,transparent 100%) top/100% var(--edge-cast-depth) no-repeat,linear-gradient(to top,#000 0%,rgba(0,0,0,.78) 18%,rgba(0,0,0,.30) 55%,transparent 100%) bottom/100% var(--edge-cast-depth) no-repeat,linear-gradient(to right,#000 0%,rgba(0,0,0,.80) 18%,rgba(0,0,0,.32) 55%,transparent 100%) left/var(--edge-cast-depth) 100% no-repeat,linear-gradient(to left,#000 0%,rgba(0,0,0,.80) 18%,rgba(0,0,0,.32) 55%,transparent 100%) right/var(--edge-cast-depth) 100% no-repeat;mask:linear-gradient(to bottom,#000 0%,rgba(0,0,0,.82) 18%,rgba(0,0,0,.34) 55%,transparent 100%) top/100% var(--edge-cast-depth) no-repeat,linear-gradient(to top,#000 0%,rgba(0,0,0,.78) 18%,rgba(0,0,0,.30) 55%,transparent 100%) bottom/100% var(--edge-cast-depth) no-repeat,linear-gradient(to right,#000 0%,rgba(0,0,0,.80) 18%,rgba(0,0,0,.32) 55%,transparent 100%) left/var(--edge-cast-depth) 100% no-repeat,linear-gradient(to left,#000 0%,rgba(0,0,0,.80) 18%,rgba(0,0,0,.32) 55%,transparent 100%) right/var(--edge-cast-depth) 100% no-repeat;animation-delay:-2.4s,-1.1s}
.edge-aurora-halo{--layer-opacity:var(--edge-halo-opacity);filter:blur(var(--edge-halo-blur)) saturate(1.30);-webkit-mask:linear-gradient(to bottom,#000 0%,rgba(0,0,0,.86) 28%,rgba(0,0,0,.34) 68%,transparent 100%) top/100% var(--edge-halo-width) no-repeat,linear-gradient(to top,#000 0%,rgba(0,0,0,.84) 28%,rgba(0,0,0,.32) 68%,transparent 100%) bottom/100% var(--edge-halo-width) no-repeat,linear-gradient(to right,#000 0%,rgba(0,0,0,.85) 28%,rgba(0,0,0,.33) 68%,transparent 100%) left/var(--edge-halo-width) 100% no-repeat,linear-gradient(to left,#000 0%,rgba(0,0,0,.85) 28%,rgba(0,0,0,.33) 68%,transparent 100%) right/var(--edge-halo-width) 100% no-repeat;mask:linear-gradient(to bottom,#000 0%,rgba(0,0,0,.86) 28%,rgba(0,0,0,.34) 68%,transparent 100%) top/100% var(--edge-halo-width) no-repeat,linear-gradient(to top,#000 0%,rgba(0,0,0,.84) 28%,rgba(0,0,0,.32) 68%,transparent 100%) bottom/100% var(--edge-halo-width) no-repeat,linear-gradient(to right,#000 0%,rgba(0,0,0,.85) 28%,rgba(0,0,0,.33) 68%,transparent 100%) left/var(--edge-halo-width) 100% no-repeat,linear-gradient(to left,#000 0%,rgba(0,0,0,.85) 28%,rgba(0,0,0,.33) 68%,transparent 100%) right/var(--edge-halo-width) 100% no-repeat;animation-delay:-1.1s,-.45s}
.edge-aurora-vignette{position:absolute;inset:0;pointer-events:none;box-shadow:inset 0 0 34px rgba(4,8,16,.18),inset 0 0 8px rgba(0,0,0,.15)}
@keyframes edgeColorOrbit{to{--edge-angle:360deg}}
@keyframes edgeLayerBreath{0%,100%{opacity:calc(var(--layer-opacity)*(1 - var(--edge-breath-strength)*.42))}50%{opacity:var(--layer-opacity)}}
.cursor-wrap{position:absolute;left:0;top:0;transform:translate3d(var(--cursor-x),var(--cursor-y),0) translate(calc(0px - var(--hotspot-x)),calc(0px - var(--hotspot-y)));width:82px;height:82px;transition:transform var(--cursor-move-speed) cubic-bezier(.18,.78,.18,1);will-change:transform;pointer-events:none}
.cursor-aura{position:absolute;left:34px;top:34px;width:var(--lab-aura-size);height:var(--lab-aura-size);border-radius:50%;transform:translate(-50%,-50%);background:radial-gradient(ellipse at 34% 28%,rgba(70,238,255,.18) 0%,rgba(102,224,255,.10) 32%,rgba(150,122,255,.06) 54%,rgba(255,156,216,.04) 64%,transparent 78%);filter:blur(var(--lab-aura-blur));opacity:var(--lab-aura-opacity);animation:cursorAura 2.2s ease-in-out infinite}
.cursor-icon{position:absolute;left:0;top:0;width:var(--lab-cursor-size);height:var(--lab-cursor-size);overflow:visible;transform-origin:var(--hotspot-x) var(--hotspot-y);animation:cursorFloatLab 2.15s ease-in-out infinite;filter:drop-shadow(0 0 5px rgba(90,236,255,.20)) drop-shadow(0 6px 10px rgba(52,35,120,.18))}
@keyframes cursorFloatLab{0%,100%{transform:translate(var(--lab-offset-x),var(--lab-offset-y)) rotate(var(--lab-rotation)) scale(var(--lab-scale-x),var(--lab-scale-y))}50%{transform:translate(var(--lab-offset-x),calc(var(--lab-offset-y) - 1px)) rotate(var(--lab-rotation)) scale(calc(var(--lab-scale-x)*1.009),calc(var(--lab-scale-y)*1.009))}}
@keyframes cursorAura{0%,100%{transform:translate(-50%,-50%) scale(.97);opacity:.44}50%{transform:translate(-50%,-50%) scale(1.07);opacity:.64}}
.cursor-hotspot{position:absolute;left:var(--hotspot-x);top:var(--hotspot-y);width:4px;height:4px;transform:translate(-50%,-50%);border-radius:50%;opacity:0}
.click-wave{position:absolute;left:var(--hotspot-x);top:var(--hotspot-y);width:24px;height:24px;border-radius:50%;border:1.5px solid rgba(112,235,255,.95);box-shadow:0 0 18px rgba(120,105,255,.20);transform:translate(-50%,-50%) scale(.25);opacity:0}
.cursor-wrap.clicking .cursor-icon{animation:cursorPress .34s cubic-bezier(.2,.8,.2,1)}
.cursor-wrap.clicking .click-wave{animation:clickWave .6s ease-out}
@keyframes cursorPress{0%{transform:scale(1)}45%{transform:scale(.94)}100%{transform:scale(1)}}
@keyframes clickWave{0%{opacity:1;transform:translate(-50%,-50%) scale(.35)}100%{opacity:0;transform:translate(-50%,-50%) scale(3.8)}}
.info-bubble{position:absolute;left:0;top:0;width:min(var(--info-bubble-width),calc(100vw - 24px));min-height:112px;padding:14px 15px 13px;border-radius:16px;background:rgba(8,17,27,.92);border:1px solid rgba(102,217,255,.20);box-shadow:0 16px 45px rgba(0,0,0,.40),0 0 26px rgba(102,217,255,.07);transform:translate(var(--bubble-x),var(--bubble-y));transition:transform var(--cursor-move-speed) cubic-bezier(.18,.78,.18,1),opacity .2s ease;contain:layout style;pointer-events:none}
.bubble-title{font-size:13px;font-weight:750;line-height:1.35;margin-bottom:7px;min-height:18px;letter-spacing:0}
.bubble-line{display:flex;justify-content:space-between;align-items:baseline;gap:12px;font-size:12px;color:var(--muted);line-height:1.7}
.bubble-line span,.bubble-line b,.bubble-title,.bubble-thought{transform:none!important;filter:none!important}
.bubble-line b{color:#e7f8ff;font-weight:600;text-align:right;overflow-wrap:anywhere}
.bubble-thought{margin-top:9px;padding-top:9px;border-top:1px solid rgba(255,255,255,.07);min-height:29px;font-size:12px;color:#c3d7e4;line-height:1.65;overflow-wrap:anywhere}
.bottom-timeline{position:absolute;left:50%;bottom:92px;transform:translateX(-50%);display:flex;align-items:center;gap:8px;padding:9px 13px;border-radius:999px;background:rgba(8,17,27,.86);border:1px solid rgba(255,255,255,.08);pointer-events:none}
.phase{display:flex;align-items:center;gap:8px;color:#7f91a2;font-size:12px;white-space:nowrap}.phase::after{content:"";width:18px;height:1px;background:rgba(255,255,255,.13)}.phase:last-child::after{display:none}.phase.active{color:#dff8ff}.phase.done{color:var(--success)}.phase i{width:6px;height:6px;border-radius:50%;background:currentColor;box-shadow:0 0 10px currentColor}
@media(max-width:720px){:root{--info-bubble-width:260px}.bottom-timeline{bottom:34px;gap:4px;padding:7px 9px}.phase{gap:4px;font-size:11px}.phase::after{width:8px}.phase i{width:4.5px;height:4.5px}}
@media(max-width:500px){:root{--info-bubble-width:232px}.bottom-timeline{display:none}.bubble-title{font-size:12.5px}.bubble-line,.bubble-thought{font-size:11.5px}}
@media(prefers-reduced-motion:reduce){.edge-aurora-layer,.cursor-aura,.cursor-icon{animation:none}.cursor-wrap,.info-bubble{transition-duration:0ms}}
</style>
</head>
<body>
<div class="app" id="app">
<section class="hud">
  <div class="edge-aurora" aria-hidden="true"><div class="edge-aurora-layer edge-aurora-cast"></div><div class="edge-aurora-layer edge-aurora-halo"></div><div class="edge-aurora-vignette"></div></div>
  <div class="cursor-wrap" id="cursor">
    <div class="cursor-aura"></div>
    <svg class="cursor-icon" viewBox="0 0 64 64" aria-hidden="true">
      <defs>
        <path id="mouseCursorShape" d="M 7.8 7.8 C 8.91 5.71, 13.83 7.17, 20.4 10.4 C 26.97 13.63, 49.02 25.34, 51.1 29.1 C 53.18 32.86, 37.42 31.38, 34.1 35.2 C 30.78 39.02, 30.93 52.78, 29.2 54.3 C 27.47 55.82, 25.14 49.77, 22.7 45.2 C 20.26 40.63, 15.36 29.87, 13.1 24.2 C 10.84 18.53, 6.69 9.89, 7.8 7.8 Z"/>
        <linearGradient id="mouseCursorBase" x1="9" y1="9" x2="42" y2="55" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#2EF1FF"/><stop offset=".23" stop-color="#69E9FF"/><stop offset=".54" stop-color="#EDF7FF"/><stop offset=".78" stop-color="#E6D9FF"/><stop offset="1" stop-color="#FF9FCE"/></linearGradient>
        <radialGradient id="mouseCursorCyan" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(33.7 16) rotate(33) scale(23 18)"><stop offset="0" stop-color="#08EAFF" stop-opacity=".95"/><stop offset=".42" stop-color="#2EDCFF" stop-opacity=".54"/><stop offset="1" stop-color="#2EDCFF" stop-opacity="0"/></radialGradient>
        <radialGradient id="mouseCursorWhite" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(29 28) rotate(32) scale(18 14)"><stop offset="0" stop-color="#FFFFFF" stop-opacity=".96"/><stop offset=".36" stop-color="#FFFFFF" stop-opacity=".80"/><stop offset=".72" stop-color="#EEF7FF" stop-opacity=".18"/><stop offset="1" stop-color="#EEF7FF" stop-opacity="0"/></radialGradient>
        <radialGradient id="mouseCursorPink" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(30 50) rotate(-70) scale(18 18)"><stop offset="0" stop-color="#FF92CD" stop-opacity=".76"/><stop offset=".46" stop-color="#B08CFF" stop-opacity=".26"/><stop offset="1" stop-color="#B08CFF" stop-opacity="0"/></radialGradient>
        <linearGradient id="mouseCursorRim" x1="9" y1="8" x2="42" y2="56" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#CCFEFF" stop-opacity=".97"/><stop offset=".30" stop-color="#78F0FF" stop-opacity=".94"/><stop offset=".66" stop-color="#C0BEFF" stop-opacity=".86"/><stop offset="1" stop-color="#FFC5E2" stop-opacity=".95"/></linearGradient>
        <clipPath id="mouseCursorClip"><use href="#mouseCursorShape"/></clipPath>
        <filter id="mouseCursorGlow" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur in="SourceGraphic" stdDeviation="1.45" result="blur"/><feColorMatrix in="blur" type="matrix" values="0 0 0 0 0.20 0 0 0 0 0.82 0 0 0 0 1 0 0 0 .18 0" result="cyanGlow"/><feMerge><feMergeNode in="cyanGlow"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
        <filter id="mouseSoftBlur" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.5"/></filter>
      </defs>
      <g filter="url(#mouseCursorGlow)"><use href="#mouseCursorShape" fill="url(#mouseCursorBase)"/><use href="#mouseCursorShape" fill="url(#mouseCursorCyan)"/><use href="#mouseCursorShape" fill="url(#mouseCursorWhite)"/><use href="#mouseCursorShape" fill="url(#mouseCursorPink)"/><use href="#mouseCursorShape" fill="none" stroke="url(#mouseCursorRim)" stroke-width="1.02" stroke-linejoin="round"/><use href="#mouseCursorShape" fill="none" stroke="rgba(255,255,255,.22)" stroke-width=".42" stroke-linejoin="round"/><g clip-path="url(#mouseCursorClip)"><ellipse cx="28.2" cy="28.1" rx="11.8" ry="8.2" fill="#FFFFFF" opacity=".09" filter="url(#mouseSoftBlur)"/><path d="M12 12.8 C20.9 14.7 33.4 20.3 45.8 27.7" fill="none" stroke="rgba(255,255,255,.44)" stroke-width=".90" stroke-linecap="round"/></g></g>
    </svg>
    <div class="cursor-hotspot"></div><div class="click-wave"></div>
  </div>
  <div class="info-bubble" id="bubble"><div class="bubble-title" id="bubbleTitle">正在开始真实填写</div><div class="bubble-line"><span>目标状态</span><b id="confidence">LIVE DOM</b></div><div class="bubble-line"><span>执行坐标</span><b id="coords">—</b></div><div class="bubble-line"><span>动作来源</span><b id="actionSource">Playwright + Live DOM</b></div><div class="bubble-thought" id="thought">正在等待第一个真实页面操作。</div></div>
  <div class="bottom-timeline" id="timeline"><div class="phase done"><i></i>观察</div><div class="phase active"><i></i>分析</div><div class="phase"><i></i>移动</div><div class="phase"><i></i>点击</div><div class="phase"><i></i>验证</div></div>
</section>
</div>
</body>
</html>"""

_INSTALL_SCRIPT = r"""
(payload) => {
  const frameId = payload.frameId;
  const apiKey = payload.apiKey;
  const oldApi = window[apiKey];
  if (oldApi && typeof oldApi.destroy === 'function') oldApi.destroy();
  const oldFrame = document.getElementById(frameId);
  if (oldFrame) oldFrame.remove();

  const frame = document.createElement('iframe');
  frame.id = frameId;
  frame.setAttribute('aria-hidden', 'true');
  frame.tabIndex = -1;
  frame.style.cssText = [
    'position:fixed','inset:0','width:100vw','height:100vh','border:0',
    'background:transparent','pointer-events:none','z-index:2147483646',
    'display:block','opacity:1','visibility:visible'
  ].join(';');
  frame.srcdoc = payload.srcdoc;
  (document.documentElement || document.body).appendChild(frame);

  const listeners = [];
  const listen = (target, name, fn, options) => {
    target.addEventListener(name, fn, options);
    listeners.push(() => target.removeEventListener(name, fn, options));
  };
  const api = {
    frame,
    currentTarget:null,
    lastActionAt:Date.now(),
    lastPulseAt:0,
    destroyed:false,
    doc(){
      try { return frame.contentDocument; } catch (_) { return null; }
    },
    nodes(){
      const d=this.doc();
      if(!d) return null;
      const nodes={
        d,
        root:d.documentElement,
        app:d.getElementById('app'),
        cursor:d.getElementById('cursor'),
        bubble:d.getElementById('bubble'),
        title:d.getElementById('bubbleTitle'),
        confidence:d.getElementById('confidence'),
        coords:d.getElementById('coords'),
        source:d.getElementById('actionSource'),
        thought:d.getElementById('thought'),
        phases:Array.from(d.querySelectorAll('.phase'))
      };
      if(!nodes.root || !nodes.app || !nodes.bubble || !nodes.title || !nodes.confidence || !nodes.coords || !nodes.source || !nodes.thought) return null;
      return nodes;
    },
    interactive(node){
      if(!(node instanceof Element)) return null;
      return node.closest('input,textarea,select,button,a,[contenteditable="true"],[role="button"],[role="checkbox"],[role="radio"],[role="combobox"],[role="listbox"],[role="option"],[role="spinbutton"],[role="slider"]');
    },
    visible(target){
      if(!(target instanceof Element) || !target.isConnected) return false;
      const rect=target.getBoundingClientRect();
      if(rect.width <= 0 || rect.height <= 0) return false;
      const style=getComputedStyle(target);
      return style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity || 1) !== 0;
    },
    label(target){
      const direct=(target.getAttribute('aria-label') || target.getAttribute('title') || '').trim();
      if(direct) return direct.slice(0,80);
      const id=(target.getAttribute('id') || '').trim();
      if(id){
        try {
          const owner=document.querySelector(`label[for="${CSS.escape(id)}"]`);
          const text=(owner && owner.innerText || '').trim();
          if(text) return text.replace(/\s+/g,' ').slice(0,80);
        } catch (_) {}
      }
      const wrap=target.closest('label');
      const wrapText=(wrap && wrap.innerText || '').trim();
      if(wrapText) return wrapText.replace(/\s+/g,' ').slice(0,80);
      const text=(target.innerText || target.getAttribute('placeholder') || target.getAttribute('name') || target.getAttribute('value') || target.tagName || '当前控件').trim();
      return text.replace(/\s+/g,' ').slice(0,80) || '当前控件';
    },
    setPhase(index){
      const nodes=this.nodes();
      if(!nodes) return;
      nodes.phases.forEach((phase,i) => {
        phase.classList.toggle('done', i < index);
        phase.classList.toggle('active', i === index);
      });
    },
    placeBubble(nodes,x,y){
      const vw=Math.max(1,window.innerWidth), vh=Math.max(1,window.innerHeight);
      const rect=nodes.bubble.getBoundingClientRect();
      const w=Math.max(1,Math.ceil(rect.width || nodes.bubble.offsetWidth || 292));
      const h=Math.max(1,Math.ceil(rect.height || nodes.bubble.offsetHeight || 132));
      const gap=28;
      let bx=x+gap, by=y+14;
      if(bx+w > vw-12) bx=x-gap-w;
      if(by+h > vh-12) by=y-gap-h;
      bx=Math.round(Math.max(12,Math.min(Math.max(12,vw-w-12),bx)));
      by=Math.round(Math.max(12,Math.min(Math.max(12,vh-h-12),by)));
      nodes.root.style.setProperty('--bubble-x',`${bx}px`);
      nodes.root.style.setProperty('--bubble-y',`${by}px`);
    },
    update(target,phase,verb,detail){
      if(this.destroyed || !this.visible(target)) return;
      const nodes=this.nodes();
      if(!nodes) return;
      this.currentTarget=target;
      this.lastActionAt=Date.now();
      const rect=target.getBoundingClientRect();
      const x=Math.max(0,Math.min(window.innerWidth,rect.left+rect.width*.5));
      const y=Math.max(0,Math.min(window.innerHeight,rect.top+rect.height*.5));
      nodes.root.style.setProperty('--cursor-x',`${x}px`);
      nodes.root.style.setProperty('--cursor-y',`${y}px`);
      nodes.coords.textContent=`${Math.round(x)}, ${Math.round(y)}`;
      nodes.title.textContent=`${verb}「${this.label(target)}」`;
      nodes.confidence.textContent='LIVE DOM';
      nodes.source.textContent='Playwright + Live DOM';
      nodes.thought.textContent=String(detail || '');
      this.placeBubble(nodes,x,y);
      this.setPhase(phase);
      nodes.app.classList.remove('hud-hidden');
    },
    pulse(){
      const nodes=this.nodes();
      if(!nodes || !nodes.cursor) return;
      nodes.cursor.classList.remove('clicking');
      void nodes.cursor.offsetWidth;
      nodes.cursor.classList.add('clicking');
      this.lastPulseAt=Date.now();
    },
    status(title,thought,phase=1){
      const nodes=this.nodes();
      if(!nodes) return false;
      nodes.title.textContent=String(title || '正在执行真实填写');
      nodes.thought.textContent=String(thought || '等待真实页面操作。');
      nodes.confidence.textContent='LIVE DOM';
      nodes.source.textContent='Listing Studio';
      this.setPhase(Math.max(0,Math.min(4,Number(phase)||0)));
      nodes.app.classList.remove('hud-hidden');
      this.lastActionAt=Date.now();
      return true;
    },
    captureSafe(active){
      frame.style.opacity=active?'0':'1';
      frame.style.visibility=active?'hidden':'visible';
    },
    destroy(){
      if(this.destroyed) return;
      this.destroyed=true;
      listeners.splice(0).forEach(off => { try { off(); } catch (_) {} });
      try { this.observer && this.observer.disconnect(); } catch (_) {}
      try { this.watchdog && clearInterval(this.watchdog); } catch (_) {}
      try { frame.remove(); } catch (_) {}
      if(window[apiKey]===this) delete window[apiKey];
    }
  };

  const onFocus=(event) => {
    const target=api.interactive(event.target);
    if(target) api.update(target,2,'正在定位','当前 live DOM 控件已获得焦点，准备执行真实写入。');
  };
  const onPointerDown=(event) => {
    const target=api.interactive(event.target);
    if(!target) return;
    api.update(target,3,'正在点击','Playwright 正在当前 live DOM 目标上执行真实点击。');
    api.pulse();
  };
  const onClick=(event) => {
    const target=api.interactive(event.target);
    if(!target) return;
    api.update(target,3,'已点击','当前 live DOM 目标已收到真实 click 事件。');
    if(Date.now()-api.lastPulseAt > 120) api.pulse();
  };
  const onInput=(event) => {
    const target=api.interactive(event.target);
    if(target) api.update(target,4,'正在验证','字段已收到真实 input 事件，等待执行器回读验证。');
  };
  const onChange=(event) => {
    const target=api.interactive(event.target);
    if(target) api.update(target,4,'正在验证','控件已收到真实 change 事件，等待执行器确认最终值。');
  };
  const reposition=() => {
    const target=api.currentTarget;
    if(target && api.visible(target)) api.update(target,2,'正在跟随','页面位置已变化，光标正在跟随当前真实目标。');
  };

  listen(document,'focusin',onFocus,true);
  listen(document,'pointerdown',onPointerDown,true);
  listen(document,'click',onClick,true);
  listen(document,'input',onInput,true);
  listen(document,'change',onChange,true);
  listen(window,'resize',reposition,{passive:true});
  listen(window,'scroll',reposition,{passive:true,capture:true});

  if(window.MutationObserver){
    api.observer=new MutationObserver(() => { api.lastActionAt=Date.now(); });
    api.observer.observe(document.documentElement,{subtree:true,childList:true,attributes:false});
  }
  api.watchdog=setInterval(() => {
    if(api.destroyed) return;
    if(Date.now()-api.lastActionAt > 75000) api.destroy();
  },5000);

  window[apiKey]=api;
  frame.addEventListener('load',() => api.status('正在开始真实填写','边缘 Aurora 已启动，等待第一个真实页面操作。',1),{once:true});
  return true;
}
"""


def _safe_evaluate(page: Any, script: str, payload: Any = None) -> Any:
    """Best-effort visual call that can never change the execution outcome."""

    try:
        if payload is None:
            return page.evaluate(script)
        return page.evaluate(script, payload)
    except Exception as exc:
        print(f"GUI_VISUAL_HUD\tERROR\t{type(exc).__name__}: {exc}", flush=True)
        return None


def install_visual_execution_hud(page: Any) -> bool:
    """Install the desktop-safe Visual Agent HUD into the owned viewport."""

    installed = bool(
        _safe_evaluate(
            page,
            _INSTALL_SCRIPT,
            {"frameId": HUD_FRAME_ID, "apiKey": HUD_API_KEY, "srcdoc": _HUD_SRCDOC},
        )
    )
    if installed:
        try:
            page.wait_for_timeout(80)
        except Exception:
            pass
    print(f"GUI_VISUAL_HUD\tINSTALLED\t{str(installed).lower()}", flush=True)
    return installed


def set_visual_execution_hud_capture_safe(page: Any, active: bool) -> None:
    _safe_evaluate(
        page,
        "([key,active]) => { const api=window[key]; if(api) api.captureSafe(active); }",
        [HUD_API_KEY, bool(active)],
    )


def finish_visual_execution_hud(page: Any, *, success: bool) -> None:
    title = "真实填写完成" if success else "真实填写未完整通过"
    thought = (
        "当前执行已完成，Listing Studio 正在整理最终报告。"
        if success
        else "当前执行已停止在未完整通过状态，请回到 Listing Studio 查看执行结果。"
    )
    _safe_evaluate(
        page,
        "([key,title,thought]) => { const api=window[key]; if(api) api.status(title,thought,4); }",
        [HUD_API_KEY, title, thought],
    )
    try:
        page.wait_for_timeout(420)
    except Exception:
        pass


def destroy_visual_execution_hud(page: Any) -> None:
    _safe_evaluate(
        page,
        "key => { const api=window[key]; if(api) api.destroy(); }",
        HUD_API_KEY,
    )


__all__ = [
    "HUD_API_KEY",
    "HUD_FRAME_ID",
    "destroy_visual_execution_hud",
    "finish_visual_execution_hud",
    "install_visual_execution_hud",
    "set_visual_execution_hud_capture_safe",
]
