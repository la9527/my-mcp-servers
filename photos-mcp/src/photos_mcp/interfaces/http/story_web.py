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
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from starlette.applications import Starlette
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from photos_mcp.application.share_image_service import ShareImageError, ShareImageService
from photos_mcp.application.story_sharing import StoryShareService, build_recommendation_story
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.infrastructure.runtime.paths import ensure_private_directory, photos_mcp_runtime_root


SESSION_COOKIE = "photos_story_session"
PUBLIC_HEADERS = {
    "Cache-Control": "no-store, private",
    "Content-Security-Policy": (
        "default-src 'none'; img-src 'self'; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow, noarchive, noimageindex",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


STORY_CSS = r"""
:root{color-scheme:light;--ink:#1d232a;--muted:#596570;--paper:#f4f1ea;--card:#fff;--line:#ddd7cc;--accent:#225b4e;--accent2:#d7ebe4;--danger:#983a36}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.55}
a{color:inherit}.shell{width:min(1120px,100%);margin:auto;padding:clamp(20px,4vw,52px) clamp(16px,3vw,36px) 72px}.eyebrow{margin:0;color:var(--accent);font-size:.78rem;font-weight:750;letter-spacing:.12em;text-transform:uppercase}
h1{font-family:ui-serif,Georgia,serif;font-size:clamp(2rem,6vw,4.6rem);line-height:1.02;letter-spacing:-.045em;margin:.35rem 0 .9rem;max-width:15ch}.lede{max-width:62ch;color:var(--muted);font-size:clamp(1rem,2vw,1.2rem);margin:0}.meta{display:flex;flex-wrap:wrap;gap:8px 18px;margin:24px 0;color:var(--muted);font-size:.9rem}
.toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:end;background:rgba(255,255,255,.7);border:1px solid var(--line);padding:14px;border-radius:18px;margin:26px 0}.toolbar label{font-size:.82rem;color:var(--muted);display:grid;gap:4px}.toolbar select{height:44px;border:1px solid var(--line);border-radius:10px;background:white;padding:0 12px}.check{display:flex!important;align-items:center;gap:8px!important;min-height:44px}.check input{width:20px;height:20px}
button,.button{min-height:44px;border:0;border-radius:999px;padding:10px 18px;font:inherit;font-weight:700;cursor:pointer;background:var(--accent);color:white;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}.secondary{background:var(--accent2);color:var(--accent)}button:focus-visible,.button:focus-visible,.tile:focus-visible{outline:3px solid #e19b38;outline-offset:3px}
.notice{border:1px solid var(--line);background:var(--card);padding:16px;border-radius:16px;margin:18px 0}.secret{font:700 1.35rem ui-monospace,monospace;letter-spacing:.16em}.copy-row{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.shares{margin:28px 0}.shares h2{font-family:ui-serif,Georgia,serif}.share-list{display:grid;gap:10px}.share-card{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;background:var(--card);border:1px solid var(--line);padding:14px 16px;border-radius:16px}.share-card p{margin:0;color:var(--muted);font-size:.84rem}.share-actions{display:flex;flex-wrap:wrap;gap:8px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:14px}.tile{border:0;background:#d9d5cd;padding:0;position:relative;aspect-ratio:1;overflow:hidden;border-radius:12px;cursor:zoom-in}.tile img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .22s ease}.tile:hover img{transform:scale(1.025)}.tile span{position:absolute;left:8px;bottom:8px;background:rgba(18,24,27,.72);color:#fff;border-radius:999px;padding:3px 8px;font-size:.7rem}
.story-status{display:inline-flex;align-items:center;gap:6px;border-radius:999px;background:var(--accent2);color:var(--accent);padding:4px 10px;font-size:.78rem;font-weight:700}.chapters{display:grid;gap:clamp(34px,6vw,68px);margin-top:34px}.chapter{border-top:1px solid var(--line);padding-top:22px}.chapter-head{display:grid;grid-template-columns:minmax(0,1fr);gap:5px;margin-bottom:16px}.chapter-date{color:var(--accent);font-size:.78rem;font-weight:750;letter-spacing:.08em}.chapter h2{font-family:ui-serif,Georgia,serif;font-size:clamp(1.6rem,4vw,2.5rem);line-height:1.1;margin:0}.chapter-copy{color:var(--muted);max-width:68ch;margin:5px 0 0}.chapter .grid{margin-top:14px}.closing{font-family:ui-serif,Georgia,serif;font-size:clamp(1.1rem,2.2vw,1.45rem);max-width:48ch;margin:50px 0 0;padding:24px 0;border-top:1px solid var(--line)}
.place-list,.location-overview{display:flex;flex-wrap:wrap;gap:7px;margin:7px 0 0}.place,.location-chip{display:inline-flex;align-items:center;gap:6px;border-radius:999px;background:var(--accent2);color:var(--accent);padding:4px 10px;font-size:.78rem;font-weight:700}.location-overview{margin:20px 0 4px}.location-chip[data-status="contextual_estimate"]{background:#eee6d4;color:#72561e}.location-chip[data-status="unknown"]{background:#e7e7e4;color:#626866}.location-subchapter{margin-top:24px}.location-subchapter h3{display:flex;align-items:center;gap:8px;font-size:1rem;margin:0;color:var(--ink)}.location-subchapter h3 span{color:var(--muted);font-size:.72rem;font-weight:600}.location-subchapter .grid{margin-top:10px}
.empty{padding:50px 20px;text-align:center;background:var(--card);border:1px solid var(--line);border-radius:20px;margin-top:30px}.lock{width:min(430px,calc(100% - 32px));margin:12vh auto;background:var(--card);border:1px solid var(--line);border-radius:24px;padding:30px;box-shadow:0 20px 60px rgba(40,35,25,.12)}.lock h1{font-size:2.2rem}.lock label{display:grid;gap:7px;color:var(--muted)}.lock input{height:50px;border:1px solid var(--line);border-radius:12px;padding:0 14px;font:1.15rem ui-monospace,monospace;letter-spacing:.12em;margin-bottom:14px;width:100%}.error{color:var(--danger)}
.viewer{border:0;padding:0;background:rgba(8,11,13,.94);color:white;width:100vw;height:100dvh;max-width:none;max-height:none}.viewer::backdrop{background:rgba(8,11,13,.94)}.viewer-inner{height:100%;display:grid;grid-template-rows:56px minmax(0,1fr) auto}.viewer-top,.viewer-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px max(14px,env(safe-area-inset-left));background:rgba(8,11,13,.82)}.viewer button,.viewer .button{background:rgba(255,255,255,.15);backdrop-filter:blur(8px)}.stage{display:grid;grid-template-columns:54px minmax(0,1fr) 54px;align-items:center;min-height:0}.stage figure{margin:0;height:100%;display:grid;place-items:center;min-width:0}.stage img{max-width:100%;max-height:100%;object-fit:contain}.nav{border-radius:50%;padding:0;width:44px;margin:auto}.caption{min-width:0}.caption strong,.caption span{display:block}.caption span{color:#c7ced2;font-size:.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.download[hidden]{display:none}.expiry{font-size:.8rem;color:var(--muted);margin-top:32px}
@media(min-width:680px){.grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.tile{border-radius:16px}}
@media(min-width:980px){.grid{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
"""


STORY_JS = r"""
(()=>{const d=document;d.querySelectorAll('[data-copy-value]').forEach(button=>button.addEventListener('click',async()=>{const original=button.textContent;try{await navigator.clipboard.writeText(button.dataset.copyValue||'');button.textContent='복사됨'}catch(_error){button.textContent='복사 실패'}setTimeout(()=>{button.textContent=original},1400)}));const dialog=d.querySelector('[data-viewer]');if(!dialog)return;const tiles=[...d.querySelectorAll('[data-photo]')],image=d.querySelector('[data-full]'),count=d.querySelector('[data-count]'),title=d.querySelector('[data-title]'),detail=d.querySelector('[data-detail]'),download=d.querySelector('[data-save]');let index=0,startX=0;
function show(next){index=(next+tiles.length)%tiles.length;const t=tiles[index];image.src=t.dataset.preview;image.alt=t.dataset.alt||'';count.textContent=`${index+1} / ${tiles.length}`;title.textContent=t.dataset.title||'사진';detail.textContent=[t.dataset.date,t.dataset.location].filter(Boolean).join(' · ');if(t.dataset.download){download.hidden=false;download.href=t.dataset.download;download.setAttribute('download','')}else{download.hidden=true;download.removeAttribute('href')}}
tiles.forEach((t,i)=>t.addEventListener('click',()=>{show(i);dialog.showModal()}));d.querySelector('[data-close]').addEventListener('click',()=>dialog.close());d.querySelector('[data-prev]').addEventListener('click',()=>show(index-1));d.querySelector('[data-next]').addEventListener('click',()=>show(index+1));dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close()});dialog.addEventListener('keydown',e=>{if(e.key==='ArrowLeft')show(index-1);if(e.key==='ArrowRight')show(index+1)});image.addEventListener('touchstart',e=>{startX=e.changedTouches[0].clientX},{passive:true});image.addEventListener('touchend',e=>{const dx=e.changedTouches[0].clientX-startX;if(Math.abs(dx)>45)show(index+(dx<0?1:-1))},{passive:true});})();
"""


def _e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _page(title: str, body: str, *, script: bool = True) -> str:
    js = '<script src="/story-assets/story.js?v=3" defer></script>' if script else ""
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        f'<title>{_e(title)}</title><link rel="stylesheet" href="/story-assets/story.css?v=3">{js}'
        f'</head><body>{body}</body></html>'
    )


def _viewer() -> str:
    return """<dialog class="viewer" data-viewer aria-label="사진 크게 보기"><div class="viewer-inner">
<header class="viewer-top"><span data-count></span><button type="button" data-close aria-label="닫기">닫기</button></header>
<div class="stage"><button class="nav" type="button" data-prev aria-label="이전 사진">‹</button><figure><img data-full alt=""></figure><button class="nav" type="button" data-next aria-label="다음 사진">›</button></div>
<footer class="viewer-foot"><div class="caption"><strong data-title></strong><span data-detail></span></div><a class="button download" data-save hidden>사진 저장</a></footer></div></dialog>"""


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
) -> str:
    asset_id = str(
        photo.get("public_asset_id") if public else photo.get("asset_id") or ""
    )
    prefix = f"/s/{share_id}/assets/{asset_id}" if public else f"/photos/assets/{asset_id}"
    download = f"{prefix}/download" if public and download_enabled else ""
    return (
        f'<button class="tile" type="button" data-photo data-preview="{_e(prefix)}/preview" '
        f'data-download="{_e(download)}" data-title="{_e(photo.get("title"))}" '
        f'data-alt="{_e(photo.get("alt"))}" data-date="{_e(photo.get("capture_date"))}" '
        f'data-location="{_e(photo.get("location"))}" aria-label="{_e(photo.get("alt") or "사진 크게 보기")}">'
        f'<img src="{_e(prefix)}/thumb" alt="{_e(photo.get("alt"))}" loading="lazy" decoding="async">'
        f'<span>{_e(photo.get("capture_date"))}</span></button>'
    )


def render_story(
    story: dict[str, Any],
    *,
    public: bool,
    share_id: str = "",
    download_enabled: bool = False,
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
        grouped_ids: set[str] = set()
        subchapters: list[str] = []
        for location_group in chapter.get("location_groups") or []:
            if not isinstance(location_group, dict):
                continue
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
                )
                for photo in group_photos
            )
            status_label = {
                "confirmed_gps": "GPS 확인",
                "contextual_estimate": "문맥 추정",
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
                )
                for photo in ungrouped
            )
            subchapters.append(
                '<section class="location-subchapter"><h3>위치 미상<span>위치 정보 없음</span></h3>'
                f'<div class="grid" aria-label="위치 미상">{cards}</div></section>'
            )
        rendered_ids.update(chapter_id_set)
        places = "".join(
            f'<span class="place">{_e(value)}</span>'
            for value in chapter.get("locations") or []
            if str(value or "")
        )
        place_list = f'<div class="place-list">{places}</div>' if places else ""
        chapter_html.append(
            '<article class="chapter">'
            '<header class="chapter-head">'
            f'<span class="chapter-date">{_e(chapter.get("date") or chapter.get("title"))}</span>'
            f'<h2>{_e(chapter.get("title") or "사진 모음")}</h2>'
            f'<p class="chapter-copy">{_e(chapter.get("summary"))}</p>'
            f'{place_list}'
            '</header>'
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
    body = (
        '<main class="shell"><p class="eyebrow">Photo story</p>'
        f'<h1>{_e(story.get("title"))}</h1><p class="lede">{_e(story.get("subtitle"))}</p>'
        f'<div class="meta"><span>{len(photos)}장</span><span>{_e(date_range)}</span>{status}</div>{overview_html}'
        f'{content}{closing}{expiry}</main>{_viewer()}'
    )
    return _page(str(story.get("title") or "사진 이야기"), body)


def render_owner(
    story: dict[str, Any],
    *,
    created: dict[str, Any] | None = None,
    passcode: str = "",
    public_base: str = "",
    active_shares: list[dict[str, Any]] | None = None,
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
    controls = """<form class="toolbar" method="post" action="/photos/share">
<label>유효 기간<select name="duration_days"><option value="30" selected>30일</option><option value="7">7일</option><option value="1">24시간</option></select></label>
<label class="check"><input type="checkbox" name="download_enabled" value="1" checked>공유본 다운로드 허용</label>
<button type="submit">공유 만들기</button></form>
<form method="post" action="/photos/story/refresh"><button class="secondary" type="submit">Linux Qwen으로 이야기 새로 구성</button></form>"""
    share_cards = []
    for shared in active_shares or []:
        share_id = str(shared.get("share_id") or "")
        if not share_id:
            continue
        url = f'{public_base.rstrip("/")}/s/{share_id}'
        download_label = "다운로드 허용" if shared.get("download_enabled") else "열람만 허용"
        share_cards.append(
            '<article class="share-card"><div>'
            f'<strong>{_e(shared.get("title") or "사진 이야기")}</strong>'
            f'<p>{_e(_display_expiry(shared.get("expires_at")))}까지 · {_e(download_label)}</p></div>'
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
    return story_html.replace('<div class="meta">', notice + controls + shares + '<div class="meta">', 1)


def render_lock(*, error: str = "") -> str:
    error_html = f'<p class="error" role="alert">{_e(error)}</p>' if error else ""
    body = f"""<main class="lock"><p class="eyebrow">Private photo story</p><h1>공유 잠금 해제</h1>
<p class="lede">공유한 사람에게 받은 잠금 코드를 입력하세요.</p>{error_html}
<form method="post"><label>잠금 코드<input name="passcode" type="password" inputmode="numeric" autocomplete="one-time-code" minlength="6" maxlength="32" required></label><button type="submit">사진 이야기 열기</button></form></main>"""
    return _page("공유 잠금 해제", body, script=False)


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
