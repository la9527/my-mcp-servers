"""Private owner gallery and minimal public shared-story web application."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
import html
import os
from pathlib import Path
import secrets
from threading import RLock
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

from starlette.applications import Starlette
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from photos_mcp.application.share_image_service import ShareImageError, ShareImageService
from photos_mcp.application.story_sharing import StoryShareService, build_recommendation_story
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.infrastructure.google_location import maps_embed_api_key
from photos_mcp.infrastructure.runtime.paths import ensure_private_directory, photos_mcp_runtime_root


SESSION_COOKIE = "photos_story_session"
PUBLIC_HEADERS = {
    "Cache-Control": "no-store, private",
    "Content-Security-Policy": (
        "default-src 'none'; img-src 'self'; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-src https://www.google.com https://maps.google.com; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow, noarchive, noimageindex",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


STORY_CSS = r"""
:root{color-scheme:light dark;--ink:#1b211f;--muted:#5f6964;--paper:#f7f5ef;--card:#fffdf8;--line:#d8dcd6;--accent:#1d6552;--accent2:#d9efe7;--danger:#a33d3d;--scrim:rgba(8,16,13,.94)}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.55}
a{color:inherit}.shell{width:min(1120px,100%);margin:auto;padding:clamp(20px,4vw,52px) clamp(16px,3vw,36px) 72px}.eyebrow{margin:0;color:var(--accent);font-size:.78rem;font-weight:750;letter-spacing:.12em;text-transform:uppercase}
h1{font-family:ui-serif,Georgia,serif;font-size:clamp(2rem,6vw,4.6rem);line-height:1.02;letter-spacing:-.045em;margin:.35rem 0 .9rem;max-width:15ch}.lede{max-width:62ch;color:var(--muted);font-size:clamp(1rem,2vw,1.2rem);margin:0}.meta{display:flex;flex-wrap:wrap;gap:8px 18px;margin:24px 0;color:var(--muted);font-size:.9rem}
.toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:end;background:rgba(255,255,255,.7);border:1px solid var(--line);padding:14px;border-radius:18px;margin:26px 0}.toolbar label{font-size:.82rem;color:var(--muted);display:grid;gap:4px}.toolbar select{height:44px;border:1px solid var(--line);border-radius:10px;background:white;padding:0 12px}.check{display:flex!important;align-items:center;gap:8px!important;min-height:44px}.check input{width:20px;height:20px}
button,.button{min-height:48px;min-width:48px;border:0;border-radius:999px;padding:10px 18px;font:inherit;font-weight:700;cursor:pointer;background:var(--accent);color:#fff;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}.secondary{background:var(--accent2);color:var(--accent)}button:focus-visible,.button:focus-visible,.tile:focus-visible{outline:3px solid #e19b38;outline-offset:3px}
.notice{border:1px solid var(--line);background:var(--card);padding:16px;border-radius:16px;margin:18px 0}.secret{font:700 1.35rem ui-monospace,monospace;letter-spacing:.16em}.copy-row{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.owner-tools,.stories,.shares{margin:28px 0}.owner-tools h2,.stories h2,.shares h2{font-family:ui-serif,Georgia,serif;margin-bottom:8px}.owner-tools>p,.stories>p{margin-top:0;color:var(--muted)}.manual-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;background:var(--card);border:1px solid var(--line);padding:16px;border-radius:18px}.manual-form label{font-size:.82rem;color:var(--muted);display:grid;gap:4px}.manual-form input[type=date],.manual-form input[type=number],.manual-form select{height:46px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);padding:0 12px;font:inherit}.manual-form .source-row,.manual-form .action-row{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:12px;align-items:center}.manual-form .source-row label{display:flex;align-items:center;gap:7px;min-height:38px}.manual-form input[type=checkbox]{width:20px;height:20px}.manual-form .action-row{justify-content:space-between}.manual-form .action-row span{color:var(--muted);font-size:.8rem}.story-list,.share-list{display:grid;gap:10px}.story-card,.share-card{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;background:var(--card);border:1px solid var(--line);padding:14px 16px;border-radius:16px}.story-card p,.share-card p{margin:0;color:var(--muted);font-size:.84rem}.share-actions{display:flex;flex-wrap:wrap;gap:8px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:14px}.tile{border:0;background:#d9d5cd;padding:0;position:relative;aspect-ratio:1;overflow:hidden;border-radius:12px;cursor:zoom-in}.tile img{width:100%;height:100%;object-fit:cover;display:block}.tile span{position:absolute;left:8px;bottom:8px;background:rgba(18,24,27,.72);color:#fff;border-radius:999px;padding:3px 8px;font-size:.7rem}
.story-status{display:inline-flex;align-items:center;gap:6px;border-radius:999px;background:var(--accent2);color:var(--accent);padding:4px 10px;font-size:.78rem;font-weight:700}.chapters{display:grid;gap:clamp(34px,6vw,68px);margin-top:34px}.chapter{border-top:1px solid var(--line);padding-top:22px}.chapter-head{display:grid;grid-template-columns:minmax(0,1fr);gap:5px;margin-bottom:16px}.chapter-date{color:var(--accent);font-size:.78rem;font-weight:750;letter-spacing:.08em}.chapter h2{font-family:ui-serif,Georgia,serif;font-size:clamp(1.6rem,4vw,2.5rem);line-height:1.1;margin:0}.chapter-copy{color:var(--muted);max-width:68ch;margin:5px 0 0}.people-caption{display:flex;align-items:center;gap:7px;color:var(--accent);font-size:.84rem;font-weight:720;margin:4px 0 0}.people-caption::before{content:"인물";border:1px solid currentColor;border-radius:999px;padding:1px 6px;font-size:.64rem;letter-spacing:.04em}.chapter .grid{margin-top:14px}.closing{font-family:ui-serif,Georgia,serif;font-size:clamp(1.1rem,2.2vw,1.45rem);max-width:48ch;margin:50px 0 0;padding:24px 0;border-top:1px solid var(--line)}
.place-list,.location-overview,.people-overview{display:flex;flex-wrap:wrap;gap:7px;margin:7px 0 0}.place,.location-chip,.person-chip{display:inline-flex;align-items:center;gap:6px;border:0;border-radius:999px;background:var(--accent2);color:var(--accent);padding:4px 10px;font-size:.78rem;font-weight:700;min-height:40px;min-width:0}.place[aria-pressed="true"],.person-chip[aria-pressed="true"]{background:var(--accent);color:#fff}.location-overview{margin:20px 0 4px}.people-overview{margin:9px 0 4px}.people-overview::before{content:"함께한 사람";display:inline-flex;align-items:center;color:var(--muted);font-size:.74rem;font-weight:700;padding-right:2px}.person-chip{background:#eee8f7;color:#5b397a}.person-filter-status{width:100%;margin:2px 0 0;color:var(--muted);font-size:.76rem}.tile[hidden]{display:none}.location-chip[data-status="contextual_estimate"]{background:#eee6d4;color:#72561e}.location-chip[data-status="unknown"]{background:#e7e7e4;color:#626866}.location-subchapter{margin-top:24px}.location-subchapter h3{display:flex;align-items:center;gap:8px;font-size:1rem;margin:0;color:var(--ink)}.location-subchapter h3 span{color:var(--muted);font-size:.72rem;font-weight:600}.location-subchapter .grid{margin-top:10px}.story-map{margin:18px 0 22px;border:1px solid var(--line);border-radius:18px;overflow:hidden;background:var(--card)}.story-map iframe{display:block;width:100%;height:min(52vw,360px);min-height:240px;border:0}.story-map-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 14px;color:var(--muted);font-size:.78rem}.story-map-foot a{font-weight:700;color:var(--accent)}.legal{display:flex;flex-wrap:wrap;gap:8px 16px;margin-top:48px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:.78rem}
.empty{padding:50px 20px;text-align:center;background:var(--card);border:1px solid var(--line);border-radius:20px;margin-top:30px}.lock{width:min(430px,calc(100% - 32px));margin:12vh auto;background:var(--card);border:1px solid var(--line);border-radius:24px;padding:30px;box-shadow:0 20px 60px rgba(40,35,25,.12)}.lock h1{font-size:2.2rem}.lock label{display:grid;gap:7px;color:var(--muted)}.lock input{height:50px;border:1px solid var(--line);border-radius:12px;padding:0 14px;font:1.15rem ui-monospace,monospace;letter-spacing:.12em;margin-bottom:14px;width:100%}.error{color:var(--danger)}
.viewer{border:0;padding:0;background:var(--scrim);color:white;width:100vw;height:100dvh;max-width:none;max-height:none}.viewer::backdrop{background:var(--scrim)}.viewer-inner{height:100%;display:grid;grid-template-rows:auto minmax(0,1fr) auto auto auto}.viewer-top,.viewer-foot{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px max(10px,env(safe-area-inset-right)) 8px max(10px,env(safe-area-inset-left));background:rgba(8,11,13,.9)}.viewer-top{justify-content:flex-end;padding-top:max(8px,env(safe-area-inset-top));min-height:64px}.viewer-foot{padding-bottom:max(8px,env(safe-area-inset-bottom))}.viewer-actions{display:flex;align-items:center;gap:4px}.viewer button,.viewer .button{background:rgba(255,255,255,.16);backdrop-filter:blur(8px)}.zoom-control{padding:0;width:48px;height:48px;border-radius:50%;font-size:1.15rem}.zoom-reset{font-size:.78rem}.stage{position:relative;display:grid;place-items:center;min-height:0;overflow:hidden;touch-action:none}.stage figure{margin:0;width:100%;height:100%;display:grid;place-items:center;min-width:0;overflow:hidden;touch-action:none;overscroll-behavior:contain}.stage img{max-width:100%;max-height:100%;object-fit:contain;transform:translate3d(0,0,0) scale(1);transform-origin:center;will-change:transform;touch-action:none;user-select:none;-webkit-user-drag:none;cursor:grab}.stage figure.is-zoomed img{cursor:grabbing}.position-indicator{display:grid;gap:5px;padding:8px max(20px,env(safe-area-inset-right)) 1px max(20px,env(safe-area-inset-left));background:rgba(8,11,13,.9)}.position-count{text-align:center;font-size:.8rem;font-weight:750;font-variant-numeric:tabular-nums}.position-track{height:3px;border-radius:999px;background:rgba(255,255,255,.24);overflow:hidden}.position-fill{display:block;width:0;height:100%;border-radius:inherit;background:#82cfb4;transition:width .18s ease}.gesture-hint{margin:0;padding:7px 16px;background:rgba(8,11,13,.9);color:#bec9c3;text-align:center;font-size:.76rem}.caption{min-width:0}.caption strong,.caption span{display:block}.caption span{color:#c7ced2;font-size:.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.download[hidden]{display:none}.expiry{font-size:.8rem;color:var(--muted);margin-top:32px}
@media(min-width:680px){.grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.tile{border-radius:16px}}
@media(min-width:980px){.grid{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media(max-width:620px){.manual-form{grid-template-columns:1fr}}
@media(hover:hover) and (pointer:fine){.tile img{transition:transform .22s ease}.tile:hover img{transform:scale(1.025)}}
@media(prefers-color-scheme:dark){:root{--ink:#eff3ee;--muted:#afb8b1;--paper:#101411;--card:#181d1a;--line:#3c4741;--accent:#82cfb4;--accent2:#214d40;--danger:#ffb4ab}.tile{background:#202622}.location-chip[data-status="unknown"]{background:#252b28;color:#c3cbc6}.location-chip[data-status="contextual_estimate"]{background:#453b24;color:#e0c47b}}
:root[data-theme="dark"]{--ink:#eff3ee;--muted:#afb8b1;--paper:#101411;--card:#181d1a;--line:#3c4741;--accent:#82cfb4;--accent2:#214d40;--danger:#ffb4ab}:root[data-theme="dark"] .tile{background:#202622}:root[data-theme="dark"] .location-chip[data-status="unknown"]{background:#252b28;color:#c3cbc6}:root[data-theme="dark"] .location-chip[data-status="contextual_estimate"]{background:#453b24;color:#e0c47b}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
"""


STORY_JS = r"""
(()=>{
const d=document;
d.querySelectorAll('[data-copy-value]').forEach(button=>button.addEventListener('click',async()=>{const original=button.textContent;try{await navigator.clipboard.writeText(button.dataset.copyValue||'');button.textContent='복사됨'}catch(_error){button.textContent='복사 실패'}setTimeout(()=>{button.textContent=original},1400)}));
d.querySelectorAll('[data-map-target]').forEach(button=>button.addEventListener('click',()=>{const chapter=button.closest('.chapter'),frame=chapter?.querySelector('[data-map-frame]'),link=chapter?.querySelector('[data-map-link]');if(frame&&button.dataset.mapTarget){frame.src=button.dataset.mapTarget;frame.title=`${button.textContent.trim()} Google 지도`}if(link&&button.dataset.mapOpen)link.href=button.dataset.mapOpen;chapter?.querySelectorAll('[data-map-target]').forEach(item=>item.setAttribute('aria-pressed',item===button?'true':'false'))}));
const dialog=d.querySelector('[data-viewer]');if(!dialog)return;
const allTiles=[...d.querySelectorAll('[data-photo]')],filterButtons=[...d.querySelectorAll('[data-person-filter]')],filterStatus=d.querySelector('[data-person-filter-status]');let tiles=[...allTiles];
function applyPersonFilter(handle,label){allTiles.forEach(tile=>{const facets=(tile.dataset.personFacets||'').split(' ').filter(Boolean);tile.hidden=Boolean(handle)&&!facets.includes(handle)});tiles=allTiles.filter(tile=>!tile.hidden);filterButtons.forEach(button=>button.setAttribute('aria-pressed',button.dataset.personFilter===handle?'true':'false'));if(filterStatus)filterStatus.textContent=handle?`${label} 사진 ${tiles.length}장`:`전체 사진 ${tiles.length}장`;if(dialog.open)dialog.close()}
filterButtons.forEach(button=>button.addEventListener('click',()=>applyPersonFilter(button.dataset.personFilter||'',button.dataset.personLabel||'선택한 인물')));
const image=dialog.querySelector('[data-full]'),figure=image.closest('figure'),count=dialog.querySelector('[data-count]'),positionTrack=dialog.querySelector('[data-position-progress]'),positionFill=dialog.querySelector('[data-position-fill]'),title=dialog.querySelector('[data-title]'),detail=dialog.querySelector('[data-detail]'),download=dialog.querySelector('[data-save]'),zoomReset=dialog.querySelector('[data-zoom-reset]');
const pointers=new Map();let index=0,scale=1,tx=0,ty=0,startX=0,startY=0,startAt=0,lastTapAt=0,lastTapX=0,lastTapY=0,pinchDistance=0,pinchScale=1,pinching=false;
const clamp=(value,minimum,maximum)=>Math.max(minimum,Math.min(maximum,value));
function panLimits(){return{x:Math.max(0,(image.offsetWidth*scale-figure.clientWidth)/2),y:Math.max(0,(image.offsetHeight*scale-figure.clientHeight)/2)}}
function renderTransform(){const limits=panLimits();tx=clamp(tx,-limits.x,limits.x);ty=clamp(ty,-limits.y,limits.y);image.style.transform=`translate3d(${tx}px,${ty}px,0) scale(${scale})`;figure.classList.toggle('is-zoomed',scale>1.01);zoomReset.textContent=scale===1?'1×':`${scale.toFixed(1)}×`;zoomReset.setAttribute('aria-label',`현재 ${scale.toFixed(1)}배, 원래 크기로`) }
function resetZoom(){scale=1;tx=0;ty=0;renderTransform()}
function setZoom(next,clientX,clientY){const previous=scale;next=clamp(next,1,4);if(Math.abs(next-previous)<.001)return;const box=figure.getBoundingClientRect(),focusX=clientX-box.left-figure.clientWidth/2,focusY=clientY-box.top-figure.clientHeight/2;tx=focusX-(focusX-tx)*(next/previous);ty=focusY-(focusY-ty)*(next/previous);scale=next;if(scale<=1.01){scale=1;tx=0;ty=0}renderTransform()}
function show(next){if(!tiles.length)return;resetZoom();index=(next+tiles.length)%tiles.length;const t=tiles[index],current=index+1;image.src=t.dataset.preview;image.alt=t.dataset.alt||'';count.textContent=`${current} / ${tiles.length}`;positionTrack.setAttribute('aria-valuemax',String(tiles.length));positionTrack.setAttribute('aria-valuenow',String(current));positionTrack.setAttribute('aria-valuetext',`${current} / ${tiles.length}`);positionFill.style.width=`${current/tiles.length*100}%`;title.textContent=t.dataset.title||'사진';detail.textContent=[t.dataset.date,t.dataset.location,t.dataset.people].filter(Boolean).join(' · ');if(t.dataset.download){download.hidden=false;download.href=t.dataset.download;download.setAttribute('download','')}else{download.hidden=true;download.removeAttribute('href')}[-1,1].forEach(offset=>{const adjacent=tiles[(index+offset+tiles.length)%tiles.length];if(adjacent){const preload=new Image();preload.src=adjacent.dataset.preview}})}
function navigate(offset){show(index+offset)}
function releaseViewer(){resetZoom();image.removeAttribute('src');image.alt=''}
allTiles.forEach(tile=>tile.addEventListener('click',()=>{const i=tiles.indexOf(tile);if(i>=0){show(i);dialog.showModal()}}));
dialog.querySelector('[data-close]').addEventListener('click',()=>dialog.close());
zoomReset.addEventListener('click',resetZoom);
dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close()});dialog.addEventListener('close',releaseViewer);
dialog.addEventListener('keydown',event=>{if(event.key==='ArrowLeft')navigate(-1);if(event.key==='ArrowRight')navigate(1);if(event.key==='+'||event.key==='=')setZoom(scale*1.5,innerWidth/2,innerHeight/2);if(event.key==='-')setZoom(scale/1.5,innerWidth/2,innerHeight/2);if(event.key==='0')resetZoom()});
figure.addEventListener('pointerdown',event=>{figure.setPointerCapture(event.pointerId);pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});if(pointers.size===1){startX=event.clientX;startY=event.clientY;startAt=performance.now();pinching=false}else if(pointers.size===2){const values=[...pointers.values()];pinchDistance=Math.hypot(values[0].x-values[1].x,values[0].y-values[1].y);pinchScale=scale;pinching=true}});
figure.addEventListener('pointermove',event=>{const previous=pointers.get(event.pointerId);if(!previous)return;pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});if(pointers.size>=2){const values=[...pointers.values()],distance=Math.hypot(values[0].x-values[1].x,values[0].y-values[1].y),centerX=(values[0].x+values[1].x)/2,centerY=(values[0].y+values[1].y)/2;if(pinchDistance>0)setZoom(pinchScale*distance/pinchDistance,centerX,centerY)}else if(scale>1.01){tx+=event.clientX-previous.x;ty+=event.clientY-previous.y;renderTransform()}});
function finishPointer(event){const previous=pointers.get(event.pointerId);if(previous)pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});const wasPinching=pinching,dx=event.clientX-startX,dy=event.clientY-startY,elapsed=performance.now()-startAt;pointers.delete(event.pointerId);if(!pointers.size){pinching=false;if(!wasPinching&&Math.abs(dx)<12&&Math.abs(dy)<12){const now=performance.now();if(now-lastTapAt<320&&Math.hypot(event.clientX-lastTapX,event.clientY-lastTapY)<36){setZoom(scale>1.01?1:2.5,event.clientX,event.clientY);lastTapAt=0}else{lastTapAt=now;lastTapX=event.clientX;lastTapY=event.clientY}}else if(scale<=1.01&&!wasPinching){const threshold=Math.max(48,figure.clientWidth*.12);if(Math.abs(dx)>threshold&&Math.abs(dx)>Math.abs(dy)*1.25&&elapsed<850){navigate(dx<0?1:-1);lastTapAt=0;return}}renderTransform()}else if(pointers.size===1){const remaining=[...pointers.values()][0];startX=remaining.x;startY=remaining.y;startAt=performance.now()}}
figure.addEventListener('pointerup',finishPointer);figure.addEventListener('pointercancel',finishPointer);figure.addEventListener('dragstart',event=>event.preventDefault());figure.addEventListener('wheel',event=>{event.preventDefault();setZoom(scale*(event.deltaY<0?1.2:1/1.2),event.clientX,event.clientY)},{passive:false});image.addEventListener('load',renderTransform);
})();
"""


def _e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _page(
    title: str,
    body: str,
    *,
    script: bool = True,
    static_base: str = "/story-assets",
) -> str:
    base = static_base.rstrip("/")
    js = f'<script src="{_e(base)}/story.js?v=6" defer></script>' if script else ""
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        f'<title>{_e(title)}</title><link rel="stylesheet" href="{_e(base)}/story.css?v=6">{js}'
        f'</head><body>{body}</body></html>'
    )


def _viewer() -> str:
    return """<dialog class="viewer" data-viewer aria-label="사진 크게 보기"><div class="viewer-inner">
<header class="viewer-top"><div class="viewer-actions"><button class="zoom-control zoom-reset" type="button" data-zoom-reset aria-label="원래 크기">1×</button><button type="button" data-close aria-label="닫기">닫기</button></div></header>
<div class="stage"><figure><img data-full alt=""></figure></div>
<div class="position-indicator"><span class="position-count" data-count aria-live="polite"></span><div class="position-track" data-position-progress role="progressbar" aria-label="현재 사진 위치" aria-valuemin="1"><span class="position-fill" data-position-fill></span></div></div>
<p class="gesture-hint">두 번 탭하거나 두 손가락으로 확대 · 기본 크기에서 좌우로 넘기기</p>
<footer class="viewer-foot"><div class="caption" aria-live="polite"><strong data-title></strong><span data-detail></span></div><a class="button download" data-save hidden>사진 저장</a></footer></div></dialog>"""


def _display_expiry(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        local = parsed.astimezone(ZoneInfo("Asia/Seoul"))
        return f"{local.year}년 {local.month}월 {local.day}일 {local:%H:%M}"
    except ValueError:
        return "정해진 시각"


def _photo_card(
    photo: dict[str, Any],
    *,
    public: bool,
    share_id: str,
    download_enabled: bool,
    asset_base: str = "",
) -> str:
    asset_id = str(
        photo.get("public_asset_id") if public else photo.get("asset_id") or ""
    )
    if public:
        prefix = f"/s/{share_id}/assets/{asset_id}"
    elif asset_base:
        prefix = f"{asset_base.rstrip('/')}/{asset_id}"
    else:
        prefix = f"/photos/assets/{asset_id}"
    download = f"{prefix}/download" if public and download_enabled else ""
    people_caption = str(photo.get("people_caption") or "").strip()
    person_facets = " ".join(
        str(value)
        for value in photo.get("person_facets") or []
        if str(value).startswith("pf_")
    )
    return (
        f'<button class="tile" type="button" data-photo data-preview="{_e(prefix)}/preview" '
        f'data-download="{_e(download)}" data-title="{_e(photo.get("title"))}" '
        f'data-alt="{_e(photo.get("alt"))}" data-date="{_e(photo.get("capture_date"))}" '
        f'data-location="{_e(photo.get("location"))}" data-people="{_e(people_caption)}" '
        f'data-person-facets="{_e(person_facets)}" '
        f'aria-label="{_e(photo.get("alt") or "사진 크게 보기")}">'
        f'<img src="{_e(prefix)}/thumb" alt="{_e(photo.get("alt"))}" loading="lazy" decoding="async">'
        f'<span>{_e(photo.get("capture_date"))}</span></button>'
    )


def _map_urls(map_payload: dict[str, Any]) -> tuple[str, str]:
    try:
        latitude = float(map_payload["latitude"])
        longitude = float(map_payload["longitude"])
    except (KeyError, TypeError, ValueError):
        return "", ""
    coordinate = f"{latitude:.7f},{longitude:.7f}"
    place_id = str(map_payload.get("google_place_id") or "").strip()
    query = f"place_id:{place_id}" if place_id else coordinate
    embed_key = maps_embed_api_key()
    embed = ""
    if embed_key:
        embed = "https://www.google.com/maps/embed/v1/place?" + urlencode(
            {"key": embed_key, "q": query, "language": "ko"}
        )
    open_params = {"api": "1", "query": coordinate}
    if place_id:
        open_params["query_place_id"] = place_id
    return embed, "https://www.google.com/maps/search/?" + urlencode(open_params)


def _chapter_map(location_groups: list[dict[str, Any]]) -> tuple[str, str]:
    options: list[tuple[str, str, str]] = []
    for group in location_groups:
        map_payload = group.get("map") if isinstance(group, dict) else None
        if not isinstance(map_payload, dict):
            continue
        embed, open_url = _map_urls(map_payload)
        if open_url:
            options.append((str(group.get("label") or "사진 위치"), embed, open_url))
    if not options:
        return "", ""
    buttons = "".join(
        f'<button class="place" type="button" data-map-target="{_e(embed)}" '
        f'data-map-open="{_e(open_url)}" aria-pressed="{"true" if index == 0 else "false"}">{_e(label)}</button>'
        for index, (label, embed, open_url) in enumerate(options)
    )
    first_label, first_embed, first_open = options[0]
    if first_embed:
        map_html = (
            '<section class="story-map" aria-label="사진 촬영 위치 지도">'
            f'<iframe data-map-frame title="{_e(first_label)} Google 지도" src="{_e(first_embed)}" '
            'loading="lazy" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>'
            '<footer class="story-map-foot"><span>Google 지도</span>'
            f'<a data-map-link href="{_e(first_open)}" target="_blank" rel="noopener noreferrer">Google 지도에서 열기</a></footer>'
            '</section>'
        )
    else:
        map_html = (
            '<p class="story-map-foot">지도를 표시할 수 없습니다. '
            f'<a data-map-link href="{_e(first_open)}" target="_blank" rel="noopener noreferrer">Google 지도에서 열기</a></p>'
        )
    return buttons, map_html


def render_story(
    story: dict[str, Any],
    *,
    public: bool,
    share_id: str = "",
    download_enabled: bool = False,
    asset_base: str = "",
    static_base: str = "/story-assets",
) -> str:
    photos = [photo for photo in story.get("photos") or [] if isinstance(photo, dict)]
    id_key = "public_asset_id" if public else "asset_id"
    photos_by_id = {str(photo.get(id_key) or ""): photo for photo in photos}
    rendered_ids: set[str] = set()
    chapter_html: list[str] = []
    for chapter in story.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        chapter_ids = [
            str(value)
            for value in chapter.get(
                "public_asset_ids" if public else "asset_ids"
            )
            or []
        ]
        chapter_photos = [
            photos_by_id[asset_id]
            for asset_id in chapter_ids
            if asset_id in photos_by_id and asset_id not in rendered_ids
        ]
        if not chapter_photos:
            continue
        chapter_id_set = {str(photo.get(id_key) or "") for photo in chapter_photos}
        location_groups = [
            group for group in chapter.get("location_groups") or [] if isinstance(group, dict)
        ]
        map_buttons, map_html = _chapter_map(location_groups)
        grouped_ids: set[str] = set()
        subchapters: list[str] = []
        for location_group in location_groups:
            group_ids = [
                str(value)
                for value in location_group.get(
                    "public_asset_ids" if public else "asset_ids"
                )
                or []
            ]
            group_photos = [
                photos_by_id[asset_id]
                for asset_id in group_ids
                if asset_id in chapter_id_set
                and asset_id in photos_by_id
                and asset_id not in grouped_ids
            ]
            if not group_photos:
                continue
            grouped_ids.update(str(photo.get(id_key) or "") for photo in group_photos)
            cards = "".join(
                _photo_card(
                    photo,
                    public=public,
                    share_id=share_id,
                    download_enabled=download_enabled,
                    asset_base=asset_base,
                )
                for photo in group_photos
            )
            status_label = {
                "confirmed_gps": "GPS 확인",
                "contextual_estimate": "위치 연결",
                "unknown": "위치 정보 없음",
            }.get(str(location_group.get("status") or ""), "")
            subchapters.append(
                '<section class="location-subchapter">'
                f'<h3>{_e(location_group.get("label") or "위치 미상")}<span>{_e(status_label)}</span></h3>'
                f'<div class="grid" aria-label="{_e(location_group.get("label") or "위치 미상")}">{cards}</div>'
                '</section>'
            )
        ungrouped = [
            photo
            for photo in chapter_photos
            if str(photo.get(id_key) or "") not in grouped_ids
        ]
        if ungrouped:
            cards = "".join(
                _photo_card(
                    photo,
                    public=public,
                    share_id=share_id,
                    download_enabled=download_enabled,
                    asset_base=asset_base,
                )
                for photo in ungrouped
            )
            subchapters.append(
                '<section class="location-subchapter"><h3>위치 미상<span>위치 정보 없음</span></h3>'
                f'<div class="grid" aria-label="위치 미상">{cards}</div></section>'
            )
        rendered_ids.update(chapter_id_set)
        if map_buttons:
            place_list = f'<div class="place-list">{map_buttons}</div>'
        else:
            places = "".join(
                f'<span class="place">{_e(value)}</span>'
                for value in chapter.get("locations") or []
                if str(value or "")
            )
            place_list = f'<div class="place-list">{places}</div>' if places else ""
        people_caption = (
            f'<p class="people-caption">{_e(chapter.get("people_intro") or chapter.get("people_caption"))}</p>'
            if chapter.get("people_intro") or chapter.get("people_caption")
            else ""
        )
        chapter_html.append(
            '<article class="chapter">'
            '<header class="chapter-head">'
            f'<span class="chapter-date">{_e(chapter.get("date") or chapter.get("title"))}</span>'
            f'<h2>{_e(chapter.get("title") or "사진 모음")}</h2>'
            f'<p class="chapter-copy">{_e(chapter.get("summary"))}</p>'
            f'{people_caption}'
            f'{place_list}'
            '</header>'
            f'{map_html}'
            f'{"".join(subchapters)}'
            '</article>'
        )
    remaining = [
        photo for photo in photos if str(photo.get(id_key) or "") not in rendered_ids
    ]
    if remaining:
        cards = "".join(
            _photo_card(
                photo,
                public=public,
                share_id=share_id,
                download_enabled=download_enabled,
                asset_base=asset_base,
            )
            for photo in remaining
        )
        chapter_html.append(
            '<article class="chapter"><header class="chapter-head">'
            '<span class="chapter-date">Archive</span><h2>사진 모음</h2>'
            f'<p class="chapter-copy">추천 사진 {len(remaining)}장입니다.</p></header>'
            f'<section class="grid" aria-label="추천 사진">{cards}</section></article>'
        )
    content = (
        f'<section class="chapters">{"".join(chapter_html)}</section>'
        if chapter_html
        else '<section class="empty"><h2>아직 추천 사진이 없습니다</h2><p>다음 자동 정리가 끝나면 이곳에 표시됩니다.</p></section>'
    )
    generation = story.get("generation") if isinstance(story.get("generation"), dict) else {}
    status = ""
    if not public and generation:
        source = (
            "Linux Qwen 편집"
            if generation.get("source") == "hermes-router"
            else "안전 기본 편집"
        )
        status = f'<span class="story-status">{_e(source)}</span>'
    date_range = " — ".join(
        value
        for value in (
            str(story.get("date_from") or ""),
            str(story.get("date_to") or ""),
        )
        if value
    )
    expiry = (
        f'<p class="expiry">이 공유는 {_e(_display_expiry(story.get("expires_at")))}에 만료됩니다.</p>'
        if public
        else ""
    )
    closing = (
        f'<p class="closing">{_e(story.get("closing"))}</p>'
        if story.get("closing")
        else ""
    )
    overview = "".join(
        f'<span class="location-chip" data-status="{_e(item.get("status"))}">'
        f'{_e(item.get("label") or "위치 미상")} · {_e(item.get("count") or 0)}장</span>'
        for item in story.get("location_overview") or []
        if isinstance(item, dict)
    )
    overview_html = (
        f'<nav class="location-overview" aria-label="위치별 사진 요약">{overview}</nav>'
        if overview
        else ""
    )
    people_filter_buttons = "".join(
        f'<button class="person-chip" type="button" data-person-filter="{_e(item.get("facet_handle"))}" '
        f'data-person-label="{_e(item.get("display_name"))}" aria-pressed="false">'
        f'{_e(item.get("display_name"))} · {_e(item.get("photo_count") or 0)}장</button>'
        for item in story.get("people_overview") or []
        if isinstance(item, dict)
        and str(item.get("display_name") or "").strip()
        and str(item.get("facet_handle") or "").startswith("pf_")
    )
    people_static = "".join(
        f'<span class="person-chip">{_e(item.get("display_name"))} · {_e(item.get("photo_count") or 0)}장</span>'
        for item in story.get("people_overview") or []
        if isinstance(item, dict)
        and str(item.get("display_name") or "").strip()
        and not str(item.get("facet_handle") or "").startswith("pf_")
    )
    people_overview_html = (
        '<nav class="people-overview" aria-label="확인된 인물별 사진 필터">'
        '<button class="person-chip" type="button" data-person-filter="" '
        'data-person-label="전체" aria-pressed="true">전체</button>'
        f'{people_filter_buttons}<span class="person-filter-status" data-person-filter-status '
        f'aria-live="polite">전체 사진 {len(photos)}장</span></nav>'
        if people_filter_buttons
        else (
            f'<nav class="people-overview" aria-label="확인된 인물별 사진 요약">{people_static}</nav>'
            if people_static else ""
        )
    )
    policy_prefix = "" if public else "/photos"
    body = (
        '<main class="shell"><p class="eyebrow">Photo story</p>'
        f'<h1>{_e(story.get("title"))}</h1><p class="lede">{_e(story.get("subtitle"))}</p>'
        f'<div class="meta"><span>{len(photos)}장</span><span>{_e(date_range)}</span>{status}</div>{overview_html}{people_overview_html}'
        f'{content}{closing}{expiry}<footer class="legal"><span>장소·지도 © Google</span>'
        f'<a href="{policy_prefix}/privacy">개인정보 안내</a>'
        f'<a href="{policy_prefix}/terms">이용 안내</a></footer></main>'
        f'{_viewer()}'
    )
    return _page(
        str(story.get("title") or "사진 이야기"),
        body,
        static_base=static_base,
    )


def render_owner(
    story: dict[str, Any],
    *,
    created: dict[str, Any] | None = None,
    passcode: str = "",
    public_base: str = "",
    active_shares: list[dict[str, Any]] | None = None,
    stories: list[dict[str, Any]] | None = None,
    recent_operations: list[dict[str, Any]] | None = None,
    notice_message: str = "",
) -> str:
    notice = ""
    if created:
        url = f'{public_base.rstrip("/")}/s/{created["share_id"]}'
        notice = (
            '<section class="notice" role="status"><strong>공유가 준비되었습니다</strong>'
            f'<p><a href="{_e(url)}">{_e(url)}</a></p><p>잠금 코드</p><p class="secret">{_e(passcode)}</p>'
            '<div class="copy-row">'
            f'<button class="secondary" type="button" data-copy-value="{_e(url)}">링크 복사</button>'
            f'<button class="secondary" type="button" data-copy-value="{_e(passcode)}">코드 복사</button>'
            '</div>'
            '<p>코드는 이 화면에서만 표시됩니다. 링크와 코드를 따로 전달하세요.</p>'
            f'<form method="post" action="/photos/shares/{_e(created["share_id"])}/revoke"><button class="secondary" type="submit">공유 즉시 종료</button></form></section>'
        )
    elif notice_message:
        notice = (
            '<section class="notice" role="status"><strong>요청을 접수했습니다</strong>'
            f'<p>{_e(notice_message)}</p></section>'
        )
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    default_from = today - timedelta(days=6)
    manual_controls = f"""<section class="owner-tools"><h2>날짜로 Story 만들기</h2>
<p>Android와 같은 분석 파이프라인을 사용합니다. 기존 분석은 재사용하고 새 사진만 분석합니다.</p>
<form class="manual-form" method="post" action="/photos/story/manual">
<label>시작일<input type="date" name="date_from" value="{default_from.isoformat()}" max="{today.isoformat()}" required></label>
<label>종료일<input type="date" name="date_to" value="{today.isoformat()}" max="{today.isoformat()}" required></label>
<div class="source-row"><label><input type="checkbox" name="source" value="apple" checked>Apple Photos</label><label><input type="checkbox" name="source" value="google" checked>Google Photos</label></div>
<label>사진 구성<select name="selection_mode"><option value="balanced" selected>균형 있게</option><option value="people_present">인물 위주</option><option value="landscape">풍경 위주</option></select></label>
<label>최대 사진 수<input type="number" name="limit" value="500" min="1" max="1000" step="1" required></label>
<div class="action-row"><span>스크린샷 제외 · 최대 6시간 · 자동 앨범 변경 없음</span><button type="submit">분석하고 Story 만들기</button></div>
</form></section>"""
    story_cards = []
    for candidate in stories or []:
        story_id = str(candidate.get("story_id") or "")
        if not story_id:
            continue
        date_range = " — ".join(
            value for value in (str(candidate.get("date_from") or ""), str(candidate.get("date_to") or "")) if value
        )
        story_cards.append(
            '<article class="story-card"><div>'
            f'<strong>{_e(candidate.get("title") or "사진 이야기")}</strong>'
            f'<p>{_e(date_range)} · {len(candidate.get("photos") or []):,}장</p></div>'
            f'<a class="button secondary" href="/photos/stories/{_e(story_id)}">Story 보기</a></article>'
        )
    story_list = (
        '<section class="stories"><h2>저장된 Story</h2><div class="story-list">'
        + "".join(story_cards)
        + "</div></section>"
        if story_cards
        else '<section class="stories"><h2>저장된 Story</h2><p>아직 표시할 Story가 없습니다.</p></section>'
    )
    operation_cards = []
    for operation in recent_operations or []:
        status = str(operation.get("status") or "")
        if status not in {"queued", "dispatching", "running"}:
            continue
        operation_cards.append(
            '<article class="story-card"><div><strong>Story 작업 진행 중</strong>'
            f'<p>{_e(operation.get("date_from"))} — {_e(operation.get("date_to"))} · {_e(status)}</p>'
            '</div></article>'
        )
    operations = (
        '<section class="stories"><h2>진행 중인 Story 작업</h2><div class="story-list">'
        + "".join(operation_cards)
        + "</div></section>"
        if operation_cards
        else ""
    )
    controls = """<form class="toolbar" method="post" action="/photos/share">
<label>유효 기간<select name="duration_days"><option value="30" selected>30일</option><option value="7">7일</option><option value="1">24시간</option></select></label>
<label class="check"><input type="checkbox" name="download_enabled" value="1" checked>공유본 다운로드 허용</label>
<label class="check"><input type="checkbox" name="include_person_names" value="1">가족 공유에 확인된 인물 이름 포함</label>
<button type="submit">공유 만들기</button></form>
<form method="post" action="/photos/story/refresh"><button class="secondary" type="submit">Linux Qwen으로 이야기 새로 구성</button></form>"""
    if not story.get("photos"):
        controls = ""
    share_cards = []
    for shared in active_shares or []:
        share_id = str(shared.get("share_id") or "")
        if not share_id:
            continue
        url = f'{public_base.rstrip("/")}/s/{share_id}'
        download_label = "다운로드 허용" if shared.get("download_enabled") else "열람만 허용"
        people_label = "인물 이름 포함" if shared.get("person_names_included") else "인물 이름 비공개"
        share_cards.append(
            '<article class="share-card"><div>'
            f'<strong>{_e(shared.get("title") or "사진 이야기")}</strong>'
            f'<p>{_e(_display_expiry(shared.get("expires_at")))}까지 · {_e(download_label)} · {_e(people_label)}</p></div>'
            '<div class="share-actions">'
            f'<a class="button secondary" href="{_e(url)}">공유 열기</a>'
            f'<form method="post" action="/photos/shares/{_e(share_id)}/revoke">'
            '<button class="secondary" type="submit">공유 종료</button></form></div></article>'
        )
    shares = (
        '<section class="shares"><h2>활성 공유</h2><div class="share-list">'
        + "".join(share_cards)
        + "</div></section>"
        if share_cards
        else ""
    )
    story_html = render_story(story, public=False)
    return story_html.replace(
        '<div class="meta">',
        notice + manual_controls + operations + story_list + controls + shares + '<div class="meta">',
        1,
    )


def render_lock(*, error: str = "") -> str:
    error_html = f'<p class="error" role="alert">{_e(error)}</p>' if error else ""
    body = f"""<main class="lock"><p class="eyebrow">Private photo story</p><h1>공유 잠금 해제</h1>
<p class="lede">공유한 사람에게 받은 잠금 코드를 입력하세요.</p>{error_html}
<form method="post"><label>잠금 코드<input name="passcode" type="password" inputmode="numeric" autocomplete="one-time-code" minlength="6" maxlength="32" required></label><button type="submit">사진 이야기 열기</button></form></main>"""
    return _page("공유 잠금 해제", body, script=False)


def render_policy_page(kind: str) -> str:
    if kind == "privacy":
        title = "사진 Story 개인정보 안내"
        copy = (
            "이 서비스는 개인·가족용 PhotosMcp가 만든 공유본을 표시합니다. "
            "Story에는 촬영 날짜, 추천 설명, 상세 장소명과 사진 GPS 기반 Google 지도가 포함될 수 있습니다. "
            "지도를 열면 좌표 또는 Google Place ID와 접속 origin이 Google Maps Platform에 전달됩니다. "
            "사진 원본은 이 페이지에서 Google로 전송하지 않으며, 공유 링크는 기본 30일 뒤 만료됩니다."
        )
    else:
        title = "사진 Story 이용 안내"
        copy = (
            "이 링크는 초대한 가족과 지인을 위한 개인 공유입니다. 링크와 잠금 코드를 제3자에게 다시 공개하지 마세요. "
            "허용된 다운로드 이미지는 원본이 아닌 공유용 파생본이며, 공유자는 만료 전에도 링크를 종료할 수 있습니다. "
            "Google 지도 내용과 장소 정보에는 Google Maps Platform의 이용 조건이 적용됩니다."
        )
    body = (
        '<main class="lock"><p class="eyebrow">PhotosMcp</p>'
        f'<h1>{_e(title)}</h1><p class="lede">{_e(copy)}</p>'
        '<p>안내를 확인한 뒤 이전 Story 탭으로 돌아가세요.</p></main>'
    )
    return _page(title, body, script=False)


class UnlockThrottle:
    def __init__(self, *, limit: int = 5, window: timedelta = timedelta(minutes=15)) -> None:
        self.limit = limit
        self.window = window
        self._failures: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = RLock()

    def allowed(self, key: str, now: datetime) -> bool:
        with self._lock:
            cutoff = now - self.window
            queue = self._failures[key]
            while queue and queue[0] <= cutoff:
                queue.popleft()
            return len(queue) < self.limit

    def fail(self, key: str, now: datetime) -> None:
        with self._lock:
            self._failures[key].append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def _state_response(state: str) -> Response:
    status = 404 if state == "missing" else 410
    return HTMLResponse(
        _page("공유를 열 수 없습니다", '<main class="lock"><h1>공유를 열 수 없습니다</h1><p>링크가 만료되었거나 공유가 종료되었습니다.</p></main>', script=False),
        status_code=status,
        headers=PUBLIC_HEADERS,
    )


def build_public_share_app(
    *,
    repository: RunRepository,
    session_secret: bytes,
    source_root: str | Path | None = None,
    cache_root: str | Path | None = None,
    now_fn=lambda: datetime.now(UTC),
) -> Starlette:
    service = StoryShareService(repository, session_secret=session_secret, now_fn=now_fn)
    images = ShareImageService(repository, source_root=source_root, cache_root=cache_root)
    throttle = UnlockThrottle()
    for expired_share_id in service.expire_due():
        images.purge_share(expired_share_id)

    async def css(_request) -> Response:
        return PlainTextResponse(STORY_CSS, media_type="text/css", headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"})

    async def js(_request) -> Response:
        return PlainTextResponse(STORY_JS, media_type="application/javascript", headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"})

    async def privacy(_request) -> Response:
        return HTMLResponse(render_policy_page("privacy"), headers=PUBLIC_HEADERS)

    async def terms(_request) -> Response:
        return HTMLResponse(render_policy_page("terms"), headers=PUBLIC_HEADERS)

    async def story(request) -> Response:
        share_id = str(request.path_params["share_id"])
        package, state = service.get_active(share_id)
        if package is None:
            if state == "expired":
                images.purge_share(share_id)
            return _state_response(state)
        token = request.cookies.get(SESSION_COOKIE, "")
        if not service.verify_session(share_id, token):
            return HTMLResponse(render_lock(), headers=PUBLIC_HEADERS)
        safe = service.public_metadata(package, include_story=True)
        return HTMLResponse(
            render_story(safe, public=True, share_id=share_id, download_enabled=bool(package.get("download_enabled"))),
            headers=PUBLIC_HEADERS,
        )

    async def unlock(request) -> Response:
        share_id = str(request.path_params["share_id"])
        package, state = service.get_active(share_id)
        if package is None:
            if state == "expired":
                images.purge_share(share_id)
            return _state_response(state)
        client = str(getattr(request.client, "host", "unknown") or "unknown")
        key = f"{share_id}:{client}"
        now = now_fn().astimezone(UTC)
        if not throttle.allowed(key, now):
            return HTMLResponse(render_lock(error="잠시 후 다시 시도해 주세요."), status_code=429, headers={**PUBLIC_HEADERS, "Retry-After": "900"})
        raw = await request.body()
        if len(raw) > 1024:
            return HTMLResponse(render_lock(error="입력값을 확인해 주세요."), status_code=413, headers=PUBLIC_HEADERS)
        values = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
        passcode = str((values.get("passcode") or [""])[0])
        if not service.verify_passcode(share_id, passcode):
            throttle.fail(key, now)
            return HTMLResponse(render_lock(error="잠금 코드가 맞지 않습니다."), status_code=401, headers=PUBLIC_HEADERS)
        throttle.clear(key)
        response = RedirectResponse(f"/s/{share_id}", status_code=303, headers=PUBLIC_HEADERS)
        response.set_cookie(
            SESSION_COOKIE,
            service.issue_session(share_id),
            max_age=12 * 60 * 60,
            httponly=True,
            secure=True,
            samesite="lax",
            path=f"/s/{share_id}",
        )
        return response

    async def asset(request) -> Response:
        share_id = str(request.path_params["share_id"])
        public_asset_id = str(request.path_params["asset_id"])
        kind = str(request.path_params["kind"])
        if kind not in {"thumb", "preview", "download"}:
            return Response(status_code=404)
        package, state = service.get_active(share_id)
        if package is None:
            if state == "expired":
                images.purge_share(share_id)
            return _state_response(state)
        if not service.verify_session(share_id, request.cookies.get(SESSION_COOKIE, "")):
            return Response(status_code=401, headers=PUBLIC_HEADERS)
        photo = service.find_photo(package, public_asset_id)
        if photo is None:
            return Response(status_code=404, headers=PUBLIC_HEADERS)
        if kind == "download" and not bool(package.get("download_enabled")):
            return Response(status_code=403, headers=PUBLIC_HEADERS)
        try:
            path = images.derivative(
                share_id=share_id,
                public_asset_id=public_asset_id,
                local_asset_id=str(photo.get("local_asset_id") or ""),
                kind=kind,  # type: ignore[arg-type]
            )
        except ShareImageError:
            return Response(status_code=404, headers=PUBLIC_HEADERS)
        headers = dict(PUBLIC_HEADERS)
        headers["Content-Type"] = "image/jpeg"
        if kind == "download":
            sequence = max(1, int(photo.get("sequence") or 1))
            headers["Content-Disposition"] = f'attachment; filename="photo-{sequence:03d}.jpg"'
        return FileResponse(path, media_type="image/jpeg", headers=headers)

    return Starlette(
        routes=[
            Route("/story-assets/story.css", css, methods=["GET"]),
            Route("/story-assets/story.js", js, methods=["GET"]),
            Route("/privacy", privacy, methods=["GET"]),
            Route("/terms", terms, methods=["GET"]),
            Route("/s/{share_id}", story, methods=["GET"]),
            Route("/s/{share_id}", unlock, methods=["POST"]),
            Route("/s/{share_id}/assets/{asset_id}/{kind}", asset, methods=["GET"]),
        ]
    )


def owner_allowed(request) -> bool:
    login = str(request.headers.get("tailscale-user-login") or "").strip().lower()
    if login:
        allowed = configured_owner_logins()
        return bool(allowed and login in allowed)
    client = str(getattr(request.client, "host", "") or "")
    return client in {"127.0.0.1", "::1", "localhost", "testclient"}


def configured_owner_logins() -> set[str]:
    configured = os.getenv("PHOTOS_MCP_OWNER_TAILSCALE_LOGINS", "").strip()
    if not configured:
        path = photos_mcp_runtime_root() / "owner-tailscale-logins"
        try:
            configured = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            configured = ""
    return {
        item.strip().lower()
        for item in configured.replace("\n", ",").split(",")
        if item.strip()
    }


def owner_mutation_allowed(request) -> bool:
    if not owner_allowed(request):
        return False
    fetch_site = str(request.headers.get("sec-fetch-site") or "").lower()
    if fetch_site == "cross-site":
        return False
    origin = str(request.headers.get("origin") or "").strip()
    if origin:
        try:
            if urlparse(origin).netloc.lower() != str(request.headers.get("host") or "").lower():
                return False
        except ValueError:
            return False
    return True


def owner_assets(
    repository: RunRepository,
    *,
    source_root: str | Path | None = None,
    cache_root: str | Path | None = None,
) -> ShareImageService:
    return ShareImageService(repository, source_root=source_root, cache_root=cache_root)


def default_public_base_url() -> str:
    return os.getenv(
        "PHOTOS_MCP_PUBLIC_SHARE_BASE_URL",
        "https://byoungyoung-macmini.tail53bcc7.ts.net:8443",
    ).rstrip("/")


def load_session_secret() -> bytes:
    configured = os.getenv("PHOTOS_MCP_SHARE_SESSION_SECRET", "").strip()
    if configured:
        value = configured.encode("utf-8")
        if len(value) < 32:
            raise ValueError("PHOTOS_MCP_SHARE_SESSION_SECRET must be at least 32 bytes")
        return value
    root = ensure_private_directory(photos_mcp_runtime_root())
    path = root / "share-session.secret"
    if path.is_file():
        value = path.read_bytes()
        if len(value) >= 32:
            return value
    value = secrets.token_bytes(48)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(value)
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)
    return value
