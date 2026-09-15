"""Private owner gallery and minimal public shared-story web application."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
import html
import os
from pathlib import Path
import secrets
import sys
from threading import RLock
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

from starlette.applications import Starlette
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from photos_mcp.application.share_image_service import ShareImageError, ShareImageService
from photos_mcp.application.story_presentation import (
    CURRENT_STORY_THEME_IDS,
    STORY_THEME_DEFINITIONS,
    automatic_story_presentation,
    normalize_story_presentation,
    owner_story_presentation,
)
from photos_mcp.interfaces.http.story_experience import EXPERIENCE_CSS, EXPERIENCE_JS
from photos_mcp.interfaces.http.story_book import BOOK_CSS, BOOK_JS
from photos_mcp.interfaces.http.story_spatial import SPATIAL_CSS, SPATIAL_JS
from photos_mcp.interfaces.http.story_gallery import GALLERY_CSS, GALLERY_JS
from photos_mcp.application.story_sharing import StoryShareService
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.infrastructure.google_location import maps_embed_api_key
from photos_mcp.infrastructure.runtime.paths import ensure_private_directory, photos_mcp_runtime_root


SESSION_COOKIE = "photos_story_session"
SWIPER_VERSION = "14.2.0"
STORY_ASSET_VERSION = "11"
SWIPER_ASSET_NAMES = frozenset(
    {"swiper-bundle.min.css", "swiper-bundle.min.js", "LICENSE"}
)
STORY_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#153cff"/><circle cx="45" cy="20" r="7" fill="#ffd325"/><path d="M10 49 25 30l10 11 7-8 12 16Z" fill="#fffdf8"/></svg>"""
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
button,.button{min-height:48px;min-width:48px;border:0;border-radius:999px;padding:10px 18px;font:inherit;font-weight:700;cursor:pointer;background:var(--accent);color:#fbfbfd;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}.secondary{background:var(--accent2);color:var(--accent)}button:focus-visible,.button:focus-visible,.tile:focus-visible{outline:3px solid #e19b38;outline-offset:3px}
.notice{border:1px solid var(--line);background:var(--card);padding:16px;border-radius:16px;margin:18px 0}.secret{font:700 1.35rem ui-monospace,monospace;letter-spacing:.16em}.copy-row{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.owner-tools,.stories,.shares{margin:28px 0}.owner-tools h2,.stories h2,.shares h2{font-family:ui-serif,Georgia,serif;margin-bottom:8px}.owner-tools>p,.stories>p{margin-top:0;color:var(--muted)}.manual-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;background:var(--card);border:1px solid var(--line);padding:16px;border-radius:18px}.manual-form label{font-size:.82rem;color:var(--muted);display:grid;gap:4px}.manual-form input[type=date],.manual-form input[type=number],.manual-form select{height:46px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);padding:0 12px;font:inherit}.manual-form .source-row,.manual-form .action-row{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:12px;align-items:center}.manual-form .source-row label{display:flex;align-items:center;gap:7px;min-height:38px}.manual-form input[type=checkbox]{width:20px;height:20px}.manual-form .action-row{justify-content:space-between}.manual-form .action-row span{color:var(--muted);font-size:.8rem}.story-list,.share-list{display:grid;gap:10px}.story-card,.share-card{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;background:var(--card);border:1px solid var(--line);padding:14px 16px;border-radius:16px}.story-card p,.share-card p{margin:0;color:var(--muted);font-size:.84rem}.share-actions{display:flex;flex-wrap:wrap;gap:8px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:14px}.tile{border:0;background:#d9d5cd;padding:0;position:relative;aspect-ratio:1;overflow:hidden;border-radius:12px;cursor:zoom-in}.tile img{width:100%;height:100%;object-fit:cover;display:block}.tile span{position:absolute;left:8px;bottom:8px;background:rgba(18,24,27,.72);color:#fff;border-radius:999px;padding:3px 8px;font-size:.7rem}
.story-status{display:inline-flex;align-items:center;gap:6px;border-radius:999px;background:var(--accent2);color:var(--accent);padding:4px 10px;font-size:.78rem;font-weight:700}.chapters{display:grid;gap:clamp(34px,6vw,68px);margin-top:34px}.chapter{border-top:1px solid var(--line);padding-top:22px}.chapter-head{display:grid;grid-template-columns:minmax(0,1fr);gap:5px;margin-bottom:16px}.chapter-date{color:var(--accent);font-size:.78rem;font-weight:750;letter-spacing:.08em}.chapter h2{font-family:ui-serif,Georgia,serif;font-size:clamp(1.6rem,4vw,2.5rem);line-height:1.1;margin:0}.chapter-copy{color:var(--muted);max-width:68ch;margin:5px 0 0}.people-caption{display:flex;align-items:center;gap:7px;color:var(--accent);font-size:.84rem;font-weight:720;margin:4px 0 0}.people-caption::before{content:"인물";border:1px solid currentColor;border-radius:999px;padding:1px 6px;font-size:.64rem;letter-spacing:.04em}.chapter .grid{margin-top:14px}.closing{font-family:ui-serif,Georgia,serif;font-size:clamp(1.1rem,2.2vw,1.45rem);max-width:48ch;margin:50px 0 0;padding:24px 0;border-top:1px solid var(--line)}
.place-list,.location-overview,.people-overview{display:flex;flex-wrap:wrap;gap:7px;margin:7px 0 0}.place,.location-chip,.person-chip{display:inline-flex;align-items:center;gap:6px;border:0;border-radius:999px;background:var(--accent2);color:var(--accent);padding:4px 10px;font-size:.78rem;font-weight:700;min-height:40px;min-width:0}.place[aria-pressed="true"],.person-chip[aria-pressed="true"]{background:var(--accent);color:#fff}.location-overview{margin:20px 0 4px}.people-overview{margin:9px 0 4px}.people-overview::before{content:"함께한 사람";display:inline-flex;align-items:center;color:var(--muted);font-size:.74rem;font-weight:700;padding-right:2px}.person-chip{background:#eee8f7;color:#5b397a}.person-filter-status{width:100%;margin:2px 0 0;color:var(--muted);font-size:.76rem}.tile[hidden]{display:none}.location-chip[data-status="contextual_estimate"]{background:#eee6d4;color:#72561e}.location-chip[data-status="unknown"]{background:#e7e7e4;color:#626866}.location-subchapter{margin-top:24px}.location-subchapter h3{display:flex;align-items:center;gap:8px;font-size:1rem;margin:0;color:var(--ink)}.location-subchapter h3 span{color:var(--muted);font-size:.72rem;font-weight:600}.location-subchapter .grid{margin-top:10px}.story-map{margin:18px 0 22px;border:1px solid var(--line);border-radius:18px;overflow:hidden;background:var(--card)}.story-map iframe{display:block;width:100%;height:min(52vw,360px);min-height:240px;border:0}.story-map-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 14px;color:var(--muted);font-size:.78rem}.story-map-foot a{font-weight:700;color:var(--accent)}.legal{display:flex;flex-wrap:wrap;gap:8px 16px;margin-top:48px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:.78rem}
.empty{padding:50px 20px;text-align:center;background:var(--card);border:1px solid var(--line);border-radius:20px;margin-top:30px}.lock{width:min(430px,calc(100% - 32px));margin:12vh auto;background:var(--card);border:1px solid var(--line);border-radius:24px;padding:30px;box-shadow:0 20px 60px rgba(40,35,25,.12)}.lock h1{font-size:2.2rem}.lock label{display:grid;gap:7px;color:var(--muted)}.lock input{height:50px;border:1px solid var(--line);border-radius:12px;padding:0 14px;font:1.15rem ui-monospace,monospace;letter-spacing:.12em;margin-bottom:14px;width:100%}.error{color:var(--danger)}
.viewer{border:0;padding:0;background:var(--scrim);color:white;width:100vw;height:100dvh;max-width:none;max-height:none}.viewer::backdrop{background:var(--scrim)}.viewer-inner{height:100%;display:grid;grid-template-rows:auto minmax(0,1fr) auto auto auto}.viewer-top,.viewer-foot{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px max(10px,env(safe-area-inset-right)) 8px max(10px,env(safe-area-inset-left));background:rgba(8,11,13,.9)}.viewer-top{justify-content:flex-end;padding-top:max(8px,env(safe-area-inset-top));min-height:64px}.viewer-foot{padding-bottom:max(8px,env(safe-area-inset-bottom))}.viewer-actions{display:flex;align-items:center;gap:4px}.viewer button,.viewer .button{background:rgba(255,255,255,.16);backdrop-filter:blur(8px)}.zoom-control{padding:0;width:48px;height:48px;border-radius:50%;font-size:.78rem}.stage.swiper{position:relative;display:block;min-width:0;min-height:0;width:100%;height:100%;overflow:hidden;overscroll-behavior:contain;background:#050807}.stage .swiper-wrapper{height:100%}.stage .swiper-slide{height:100%;display:flex;align-items:center;justify-content:center;overflow:hidden}.stage .swiper-zoom-container{width:100%;height:100%;display:flex;align-items:center;justify-content:center}.stage img{display:block;width:auto;height:auto;max-width:none;max-height:none;object-fit:contain;user-select:none;-webkit-user-drag:none;opacity:1;transition:opacity .12s ease}.stage .is-loading img{opacity:0}.stage .swiper-slide-zoomed img{cursor:grab}.position-indicator{display:grid;gap:5px;padding:8px max(20px,env(safe-area-inset-right)) 1px max(20px,env(safe-area-inset-left));background:rgba(8,11,13,.9)}.position-count{text-align:center;font-size:.8rem;font-weight:750;font-variant-numeric:tabular-nums}.position-track{height:3px;border-radius:999px;background:rgba(255,255,255,.24);overflow:hidden}.position-fill{display:block;width:0;height:100%;border-radius:inherit;background:#82cfb4;transition:width .18s ease}.gesture-hint{margin:0;padding:7px 16px;background:rgba(8,11,13,.9);color:#bec9c3;text-align:center;font-size:.76rem}.caption{min-width:0}.caption strong,.caption span{display:block}.caption span{color:#c7ced2;font-size:.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.download[hidden]{display:none}.expiry{font-size:.8rem;color:var(--muted);margin-top:32px}
@media(min-width:680px){.grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.tile{border-radius:16px}}
@media(min-width:980px){.grid{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media(max-width:620px){.manual-form{grid-template-columns:1fr}.manual-form .action-row{display:grid;grid-template-columns:1fr;justify-items:stretch}.manual-form .action-row button{width:100%;white-space:normal}.manual-form .source-row{align-items:flex-start}}
@media(hover:hover) and (pointer:fine){.tile img{transition:transform .22s ease}.tile:hover img{transform:scale(1.025)}}
@media(prefers-color-scheme:dark){:root{--ink:#eff3ee;--muted:#afb8b1;--paper:#101411;--card:#181d1a;--line:#3c4741;--accent:#82cfb4;--accent2:#214d40;--danger:#ffb4ab}.tile{background:#202622}.location-chip[data-status="unknown"]{background:#252b28;color:#c3cbc6}.location-chip[data-status="contextual_estimate"]{background:#453b24;color:#e0c47b}}
:root[data-theme="dark"]{--ink:#eff3ee;--muted:#afb8b1;--paper:#101411;--card:#181d1a;--line:#3c4741;--accent:#82cfb4;--accent2:#214d40;--danger:#ffb4ab}:root[data-theme="dark"] .tile{background:#202622}:root[data-theme="dark"] .location-chip[data-status="unknown"]{background:#252b28;color:#c3cbc6}:root[data-theme="dark"] .location-chip[data-status="contextual_estimate"]{background:#453b24;color:#e0c47b}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important;animation:none!important}}

/* Story presentation v1. Narrative theme and visual theme intentionally stay separate. */
html[data-story-theme] body{min-height:100dvh;background:var(--paper);color:var(--ink)}
html[data-story-theme] .shell{position:relative}
html[data-story-theme]:not([data-story-theme="map_journey"]) .story-map iframe{height:min(42vw,240px);min-height:180px}
.presentation-panel{margin:22px 0 30px;border:1px solid var(--line);background:var(--card);border-radius:16px;overflow:hidden}
.presentation-panel>summary{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:52px;padding:12px 16px;cursor:pointer;font-weight:800;list-style:none}.presentation-panel>summary::-webkit-details-marker{display:none}.presentation-panel>summary::after{content:"스타일 선택";color:var(--accent);font-size:.78rem;font-weight:750}.presentation-panel[open]>summary{border-bottom:1px solid var(--line)}
.presentation-content{padding:16px}.presentation-intro{margin:0 0 12px;color:var(--muted);font-size:.8rem}
.theme-choices{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}
.theme-choice{position:relative;display:grid;align-content:start;gap:6px;min-height:112px;padding:14px;border:1px solid var(--line);border-radius:12px;background:var(--paper);cursor:pointer;transition:transform .18s ease,border-color .18s ease,background-color .18s ease}
.theme-choice input{position:absolute;opacity:0;pointer-events:none}.theme-choice strong{font-size:.9rem;line-height:1.2}.theme-choice span{color:var(--muted);font-size:.72rem;line-height:1.35}.theme-choice::before{content:"";display:block;width:34px;height:8px;background:var(--choice-accent,var(--accent));border-radius:2px}
.theme-choice:has(input:checked){border-color:var(--accent);box-shadow:inset 0 0 0 2px var(--accent)}
.theme-choice:has(input:focus-visible){outline:3px solid #ffad0a;outline-offset:3px}
.theme-choice[data-choice="journal"]{--choice-accent:#153cff}.theme-choice[data-choice="cinema"]{--choice-accent:#ffad0a}.theme-choice[data-choice="memory_book"]{--choice-accent:#ff6b55}.theme-choice[data-choice="map_journey"]{--choice-accent:#2747ff}.theme-choice[data-choice="film_index"]{--choice-accent:#f03b3b}
.presentation-action{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:12px}.presentation-action span{color:var(--muted);font-size:.76rem}
.overview-disclosure{margin:10px 0}.overview-disclosure>summary{width:max-content;max-width:100%;cursor:pointer;color:var(--accent);font-size:.82rem;font-weight:760}.overview-disclosure[open]>summary{margin-bottom:10px}
.theme-gallery{min-width:0}.theme-pagination{display:none}.theme-gallery.swiper{overflow:hidden}.theme-gallery.swiper .grid{display:flex;margin:0}.theme-gallery.swiper .tile{height:auto;flex-shrink:0}.theme-gallery.swiper .theme-pagination{display:flex;justify-content:center;gap:7px;padding:14px 0 2px}.theme-gallery.swiper .swiper-pagination-bullet{width:7px;height:7px;background:currentColor;opacity:.28}.theme-gallery.swiper .swiper-pagination-bullet-active{opacity:1}

/* Cobalt poster: asymmetric editorial blocks, primary default. */
html[data-story-theme="journal"]{--ink:#12141a;--muted:#515864;--paper:#f4f0e7;--card:#fffdf7;--line:#181b22;--accent:#153cff;--accent2:#dfe5ff;--danger:#c93434}
html[data-story-theme="journal"] .shell{width:min(1240px,100%);padding-top:clamp(28px,5vw,68px)}
html[data-story-theme="journal"] .eyebrow{display:inline-block;padding:6px 9px;background:#ffd325;color:#111;letter-spacing:.06em}
html[data-story-theme="journal"] h1{font-family:system-ui,-apple-system,sans-serif;font-size:clamp(3rem,7vw,6.4rem);font-weight:900;max-width:16ch;overflow-wrap:anywhere;letter-spacing:-.07em;line-height:.88;margin:.55rem 0 1.2rem}
html[data-story-theme="journal"] .lede{font-weight:650;color:var(--ink);border-left:10px solid #ff3e2f;padding-left:16px}
html[data-story-theme="journal"] .meta{border-top:2px solid var(--ink);border-bottom:2px solid var(--ink);padding:12px 0;text-transform:uppercase;font-weight:750}
html[data-story-theme="journal"] .chapter{border-top:5px solid var(--ink);padding-top:18px}
html[data-story-theme="journal"] .chapter:nth-child(even){margin-left:clamp(0px,8vw,110px)}
html[data-story-theme="journal"] .chapter h2{font-family:system-ui,-apple-system,sans-serif;font-weight:900;letter-spacing:-.045em}
html[data-story-theme="journal"] .tile{border-radius:0;border:2px solid var(--ink);box-shadow:6px 6px 0 var(--accent)}
html[data-story-theme="journal"] .story-map{border:2px solid var(--ink);border-radius:0;box-shadow:8px 8px 0 #ffd325}
html[data-story-theme="journal"] button,html[data-story-theme="journal"] .button{border-radius:4px}

/* Darkroom cinema: immersive horizontal sequences with restrained amber controls. */
html[data-story-theme="cinema"]{color-scheme:dark;--ink:#f3edf4;--muted:#b7abb9;--paper:#0b0710;--card:#17101c;--line:#493850;--accent:#ffad0a;--accent2:#352517;--danger:#ff8177}
html[data-story-theme="cinema"] .shell{width:min(1440px,100%);padding-inline:clamp(18px,4vw,64px)}
html[data-story-theme="cinema"] .eyebrow{color:var(--accent);letter-spacing:.18em}
html[data-story-theme="cinema"] h1{font-family:system-ui,-apple-system,sans-serif;font-weight:780;font-size:clamp(3rem,7vw,6.4rem);max-width:17ch;overflow-wrap:anywhere;line-height:.92}
html[data-story-theme="cinema"] .lede{color:#d1c6d3}
html[data-story-theme="cinema"] .chapter{border:0;padding:clamp(34px,7vw,88px) 0}
html[data-story-theme="cinema"] .chapter-head{max-width:760px}
html[data-story-theme="cinema"] .chapter h2{font-family:system-ui,-apple-system,sans-serif;font-size:clamp(2.3rem,6vw,5.4rem);font-weight:760;letter-spacing:-.055em}
html[data-story-theme="cinema"] .theme-gallery.swiper{margin-top:18px;margin-inline:calc(clamp(18px,4vw,64px)*-1);padding-inline:clamp(18px,4vw,64px)}
html[data-story-theme="cinema"] .theme-gallery.swiper .tile{width:min(82vw,920px);aspect-ratio:16/10;border-radius:8px;margin-right:clamp(12px,2vw,28px);background:#19121e}
html[data-story-theme="cinema"] .tile span{border-radius:3px;background:rgba(11,7,16,.82)}
html[data-story-theme="cinema"] .story-map{background:#17101c;border-color:#493850}
html[data-story-theme="cinema"] button,html[data-story-theme="cinema"] .button{color:#18100a}

/* Pop-up playbook: people-first family collage, coral interaction and soft color fields. */
html[data-story-theme="memory_book"]{--ink:#1c2440;--muted:#53607c;--paper:#eef5ff;--card:#fbfcff;--line:#26345a;--accent:#e84636;--accent2:#ffd9d3;--danger:#b62a30}
html[data-story-theme="memory_book"] body{background:linear-gradient(180deg,#b9d8ff 0 23rem,#eef5ff 23rem)}
html[data-story-theme="memory_book"] .shell{width:min(1200px,100%)}
html[data-story-theme="memory_book"] h1{font-family:system-ui,-apple-system,sans-serif;font-weight:900;letter-spacing:-.06em;max-width:16ch;overflow-wrap:anywhere;transform:rotate(-1deg)}
html[data-story-theme="memory_book"] .eyebrow{color:#1c2440;background:#ffe34d;padding:5px 10px;border:2px solid #1c2440;border-radius:5px;display:inline-block}
html[data-story-theme="memory_book"] .chapter{border:2px solid #26345a;background:#fbfcff;padding:clamp(18px,3vw,34px);box-shadow:10px 10px 0 #cdb8ff;border-radius:16px}
html[data-story-theme="memory_book"] .chapter:nth-child(even){box-shadow:10px 10px 0 #ffe34d;transform:rotate(.35deg)}
html[data-story-theme="memory_book"] .chapter h2{font-family:system-ui,-apple-system,sans-serif;font-weight:900}
html[data-story-theme="memory_book"] .grid{grid-template-columns:repeat(12,minmax(0,1fr));grid-auto-flow:dense}
html[data-story-theme="memory_book"] .tile{grid-column:span 6;border:2px solid #26345a;border-radius:12px}
html[data-story-theme="memory_book"] .tile:nth-child(5n+1){grid-column:span 7;aspect-ratio:4/3}html[data-story-theme="memory_book"] .tile:nth-child(5n+2){grid-column:span 5}
html[data-story-theme="memory_book"] button,html[data-story-theme="memory_book"] .button{border-radius:8px}

/* Transit atlas: map-led split composition and location carousel. */
html[data-story-theme="map_journey"]{--ink:#eff4ff;--muted:#b4c0e3;--paper:#0d1638;--card:#14214b;--line:#415383;--accent:#ff6a2a;--accent2:#27355f;--danger:#ff887f;color-scheme:dark}
html[data-story-theme="map_journey"] body{background:linear-gradient(90deg,#0d1638,#101c47)}
html[data-story-theme="map_journey"] .shell{width:min(1320px,100%)}
html[data-story-theme="map_journey"] h1{font-family:system-ui,-apple-system,sans-serif;font-weight:850;letter-spacing:-.06em;max-width:18ch;overflow-wrap:anywhere}
html[data-story-theme="map_journey"] .eyebrow{color:#9db9ff}
html[data-story-theme="map_journey"] .chapter{border-top:1px solid #415383;padding-top:32px}
html[data-story-theme="map_journey"] .chapter h2{font-family:system-ui,-apple-system,sans-serif;font-weight:820}
html[data-story-theme="map_journey"] .chapter:has(.story-map){display:grid;grid-template-columns:minmax(280px,.8fr) minmax(0,1.4fr);gap:clamp(24px,4vw,60px);align-items:start}
html[data-story-theme="map_journey"] .chapter:has(.story-map) .chapter-head,html[data-story-theme="map_journey"] .chapter:has(.story-map) .story-map{grid-column:1}
html[data-story-theme="map_journey"] .chapter:has(.story-map) .story-map{position:sticky;top:18px;margin:0}
html[data-story-theme="map_journey"] .chapter:has(.story-map) .location-subchapter{grid-column:2}
html[data-story-theme="map_journey"] .theme-gallery.swiper .tile{width:min(72vw,560px);aspect-ratio:4/3;border-radius:4px;margin-right:14px}
html[data-story-theme="map_journey"] button,html[data-story-theme="map_journey"] .button{border-radius:4px}

/* Silver index: compact contact sheet for large libraries. */
html[data-story-theme="film_index"]{--ink:#121317;--muted:#555b65;--paper:#d9dde3;--card:#eef0f3;--line:#8b929d;--accent:#d72f35;--accent2:#f6cfd1;--danger:#a61f27}
html[data-story-theme="film_index"] .shell{width:min(1540px,100%);padding-inline:clamp(12px,2vw,30px)}
html[data-story-theme="film_index"] h1{font:850 clamp(2.5rem,6vw,5.8rem)/.92 system-ui,-apple-system,sans-serif;letter-spacing:-.07em;max-width:18ch;overflow-wrap:anywhere}
html[data-story-theme="film_index"] .eyebrow{font-family:ui-monospace,monospace;color:var(--accent)}
html[data-story-theme="film_index"] .chapter{border-top:3px solid var(--ink);padding-top:12px}
html[data-story-theme="film_index"] .chapter-head{grid-template-columns:minmax(220px,.45fr) minmax(0,1fr);align-items:end}
html[data-story-theme="film_index"] .chapter-copy{margin:0}
html[data-story-theme="film_index"] .chapter h2{font-family:system-ui,-apple-system,sans-serif;font-weight:850}
html[data-story-theme="film_index"] .grid{grid-template-columns:repeat(5,minmax(0,1fr));gap:6px}
html[data-story-theme="film_index"] .tile{border-radius:0;aspect-ratio:1;border:1px solid #8b929d}
html[data-story-theme="film_index"] .tile span{left:4px;bottom:4px;border-radius:0;font:600 .62rem ui-monospace,monospace}
html[data-story-theme="film_index"] button,html[data-story-theme="film_index"] .button{border-radius:2px}

@media(min-width:1180px){html[data-story-theme="film_index"] .grid{grid-template-columns:repeat(8,minmax(0,1fr))}}
@media(max-width:860px){.theme-choices{grid-template-columns:1fr 1fr}.theme-choice:last-child{grid-column:1/-1}html[data-story-theme="map_journey"] .chapter:has(.story-map){display:block}html[data-story-theme="map_journey"] .chapter:has(.story-map) .story-map{position:relative;top:auto;margin:18px 0 22px}}
@media(max-width:679px){html[data-story-theme] h1{width:100%;max-width:calc(100vw - 32px);font-size:clamp(2rem,9vw,2.45rem);line-height:.96;letter-spacing:-.05em;overflow-wrap:anywhere;word-break:break-all;white-space:normal}html[data-story-theme] h1 .title-range-start,html[data-story-theme] h1 .title-range-end{display:block}html[data-story-theme="journal"] .chapter:nth-child(even){margin-left:0}html[data-story-theme="memory_book"] .grid{grid-template-columns:repeat(2,minmax(0,1fr))}html[data-story-theme="memory_book"] .tile,html[data-story-theme="memory_book"] .tile:nth-child(n){grid-column:span 1}html[data-story-theme="film_index"] .chapter-head{grid-template-columns:1fr;align-items:start}html[data-story-theme="film_index"] .grid{grid-template-columns:repeat(3,minmax(0,1fr))}.presentation-action{align-items:stretch;flex-direction:column}.presentation-action button{width:100%}}
@media(max-width:679px){html[data-story-theme]:not([data-story-theme="map_journey"]) .story-map iframe{height:190px;min-height:190px}}
@media(prefers-reduced-motion:reduce){.theme-choice{transition:none!important}}
"""


STORY_CSS += EXPERIENCE_CSS + BOOK_CSS + SPATIAL_CSS + GALLERY_CSS
STORY_JS = BOOK_JS + SPATIAL_JS + GALLERY_JS + EXPERIENCE_JS


def swiper_asset_path(name: str) -> Path | None:
    """Resolve a pinned Swiper asset in source checkouts and packaged app bundles."""

    if name not in SWIPER_ASSET_NAMES:
        return None
    candidates = (
        Path(__file__).resolve().parents[4]
        / "resources"
        / "web"
        / f"swiper-{SWIPER_VERSION}"
        / name,
        Path(sys.executable).resolve().parent.parent / "Resources" / "story-assets" / name,
    )
    return next(
        (path for path in candidates if path.is_file() and path.stat().st_size > 0),
        None,
    )


def swiper_asset_response(name: str) -> Response:
    path = swiper_asset_path(name)
    if path is None:
        return Response(status_code=404, headers={"X-Content-Type-Options": "nosniff"})
    media_type = {
        ".css": "text/css",
        ".js": "application/javascript",
    }.get(path.suffix, "text/plain")
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


def story_icon_response() -> Response:
    return Response(
        STORY_ICON_SVG,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _story_heading(value: Any) -> str:
    """Keep long date-range titles readable in narrow WebViews."""

    title = str(value or "")
    if " — " not in title:
        return _e(title)
    start, end = title.split(" — ", 1)
    return (
        f'<span class="title-range-start">{_e(start)}</span>'
        f'<span class="title-range-end"> — {_e(end)}</span>'
    )


def _page(
    title: str,
    body: str,
    *,
    script: bool = True,
    static_base: str = "/story-assets",
    story_theme: str = "",
    design_preset: str = "",
) -> str:
    base = static_base.rstrip("/")
    vendor_css = (
        f'<link rel="stylesheet" href="{_e(base)}/swiper-bundle.min.css?v={SWIPER_VERSION}">'
        if script
        else ""
    )
    js = (
        f'<script src="{_e(base)}/swiper-bundle.min.js?v={SWIPER_VERSION}" defer></script>'
        f'<script src="{_e(base)}/story.js?v={STORY_ASSET_VERSION}" defer></script>'
        if script
        else ""
    )
    root_attributes = (
        f' data-story-theme="{_e(story_theme)}" data-design-preset="{_e(design_preset)}"'
        if story_theme
        else ""
    )
    return (
        f'<!doctype html><html lang="ko"{root_attributes}><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        f'<link rel="icon" href="{_e(base)}/favicon.svg" type="image/svg+xml">'
        f'<title>{_e(title)}</title>{vendor_css}'
        f'<link rel="stylesheet" href="{_e(base)}/story.css?v={STORY_ASSET_VERSION}">{js}'
        f'</head><body>{body}</body></html>'
    )


def _viewer() -> str:
    return """<dialog class="viewer" data-viewer aria-label="사진 크게 보기"><div class="viewer-inner">
<header class="viewer-top"><button type="button" data-grid-toggle>전체 사진</button><div class="viewer-actions"><button class="zoom-control zoom-reset" type="button" data-zoom-reset aria-label="화면에 맞춤" hidden>화면에 맞춤</button><button type="button" data-close aria-label="닫기">닫기</button></div></header>
<div class="stage swiper" data-swiper role="region" aria-label="사진 슬라이드"><div class="swiper-wrapper" data-swiper-wrapper></div></div>
<div class="viewer-all-grid" data-all-grid role="region" aria-label="전체 사진" hidden></div>
<nav class="viewer-filmstrip" data-filmstrip aria-label="주변 사진"></nav>
<div class="position-indicator"><span class="position-count" data-count aria-live="polite"></span><div class="position-track" data-position-progress role="progressbar" aria-label="현재 사진 위치" aria-valuemin="1"><span class="position-fill" data-position-fill></span></div></div>
<p class="gesture-hint">좌우로 넘기기 · 두 번 탭하거나 두 손가락으로 확대</p>
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


def _theme_picker(
    presentation: dict[str, Any],
    *,
    action: str,
    story_id: str,
) -> str:
    if not action or not story_id:
        return ""
    selected = str(presentation.get("theme_id") or "journal")
    choices = "".join(
        '<label class="theme-choice" '
        f'data-choice="{_e(item["theme_id"])}">'
        f'<input type="radio" name="theme_id" value="{_e(item["theme_id"])}" '
        f'{"checked" if item["theme_id"] == selected else ""}>'
        f'<strong>{_e(item["display_name"])}</strong>'
        f'<span>{_e(item["description"])}</span></label>'
        for item in STORY_THEME_DEFINITIONS
        if item["theme_id"] in CURRENT_STORY_THEME_IDS
    )
    selected_name = next(
        (
            item["display_name"]
            for item in STORY_THEME_DEFINITIONS
            if item["theme_id"] == selected
        ),
        "스크롤 시네마",
    )
    return (
        '<details class="presentation-panel">'
        f'<summary><span>Story 스타일</span><strong data-selected-theme>{_e(selected_name)}</strong></summary>'
        '<div class="presentation-content"><p class="presentation-intro">'
        '사진은 그대로 두고 표현 방식만 바꿉니다.</p>'
        f'<form method="post" action="{_e(action)}">'
        f'<input type="hidden" name="story_id" value="{_e(story_id)}">'
        f'<input type="hidden" name="presentation_revision" value="{_e(presentation.get("presentation_revision") or 1)}">'
        f'<div class="theme-choices">{choices}</div>'
        '<div class="presentation-action"><span>선택한 스타일은 재분석 뒤에도 유지됩니다.</span>'
        '<button type="submit">스타일 적용</button></div></form></div></details>'
    )


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
        f'<button class="tile" type="button" data-photo data-preview="{_e(prefix)}/preview" data-thumb="{_e(prefix)}/gallery" '
        f'data-download="{_e(download)}" data-title="{_e(photo.get("title"))}" '
        f'data-alt="{_e(photo.get("alt"))}" data-date="{_e(photo.get("capture_date"))}" '
        f'data-location="{_e(photo.get("location"))}" data-people="{_e(people_caption)}" '
        f'data-person-facets="{_e(person_facets)}" '
        f'aria-label="{_e(photo.get("alt") or "사진 크게 보기")}">'
        f'<img src="{_e(prefix)}/thumb" alt="{_e(photo.get("alt"))}" loading="lazy" decoding="async">'
        f'<span>{_e(photo.get("capture_date"))}</span></button>'
    )


def _photo_gallery(cards: str, *, label: str) -> str:
    return (
        '<div class="theme-gallery" data-theme-gallery>'
        f'<div class="grid" data-theme-gallery-track role="group" aria-label="{_e(label)}">{cards}</div>'
        '<div class="theme-pagination" data-theme-pagination aria-hidden="true"></div>'
        '<div class="gallery-controls"><button type="button" data-gallery-prev aria-label="이전 사진">이전</button>'
        '<span class="gallery-count" data-gallery-count aria-live="polite"></span>'
        '<button type="button" data-gallery-next aria-label="다음 사진">다음</button></div>'
        '</div>'
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
    presentation: dict[str, Any] | None = None,
    theme_action: str = "",
) -> str:
    visual = normalize_story_presentation(
        presentation or story.get("presentation") or automatic_story_presentation(story)
    )
    if not public:
        visual = owner_story_presentation(visual)
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
                ('<section class="location-subchapter" data-location-unknown>'
                 if str(location_group.get("status") or "") == "unknown"
                 else '<section class="location-subchapter">')
                +
                f'<h3>{_e(location_group.get("label") or "위치 미상")}<span>{_e(status_label)}</span></h3>'
                f'{_photo_gallery(cards, label=str(location_group.get("label") or "위치 미상"))}'
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
                '<section class="location-subchapter" data-location-unknown><h3>위치 미상<span>위치 정보 없음</span></h3>'
                f'{_photo_gallery(cards, label="위치 미상")}</section>'
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
            '</header>'
            f'{"".join(subchapters)}'
            + (f'<details class="chapter-details"><summary>촬영 장소와 지도</summary>{place_list}{map_html}</details>'
               if place_list or map_html else '') +
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
            f'{_photo_gallery(cards, label="추천 사진")}</article>'
        )
    content = (
        f'<section class="chapters">{"".join(chapter_html)}</section>'
        if chapter_html
        else '<section class="empty"><h2>아직 추천 사진이 없습니다</h2><p>다음 자동 정리가 끝나면 이곳에 표시됩니다.</p></section>'
    )
    date_range = " - ".join(
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
    location_overview_items = [
        item
        for item in story.get("location_overview") or []
        if isinstance(item, dict)
    ]
    overview = "".join(
        f'<span class="location-chip" data-status="{_e(item.get("status"))}">'
        f'{_e(item.get("label") or "위치 미상")} · {_e(item.get("count") or 0)}장</span>'
        for item in location_overview_items
    )
    overview_html = (
        (
            '<details class="overview-disclosure"><summary>'
            f'장소 {len(location_overview_items)}곳 보기</summary>'
            f'<nav class="location-overview" aria-label="위치별 사진 요약">{overview}</nav>'
            '</details>'
            if len(location_overview_items) > 6
            else f'<nav class="location-overview" aria-label="위치별 사진 요약">{overview}</nav>'
        )
        if overview
        else ""
    )
    people_items = [
        item
        for item in story.get("people_overview") or []
        if isinstance(item, dict) and str(item.get("display_name") or "").strip()
    ]
    people_filter_buttons = "".join(
        f'<button class="person-chip" type="button" data-person-filter="{_e(item.get("facet_handle"))}" '
        f'data-person-label="{_e(item.get("display_name"))}" aria-pressed="false">'
        f'{_e(item.get("display_name"))} · {_e(item.get("photo_count") or 0)}장</button>'
        for item in people_items
        if str(item.get("display_name") or "").strip()
        and str(item.get("facet_handle") or "").startswith("pf_")
    )
    people_static = "".join(
        f'<span class="person-chip">{_e(item.get("display_name"))} · {_e(item.get("photo_count") or 0)}장</span>'
        for item in people_items
        if str(item.get("display_name") or "").strip()
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
    if people_overview_html and len(people_items) > 6:
        people_overview_html = (
            '<details class="overview-disclosure"><summary>'
            f'함께한 사람 {len(people_items)}명 보기</summary>'
            f'{people_overview_html}</details>'
        )
    policy_prefix = "" if public else "/photos"
    picker = (
        _theme_picker(
            visual,
            action=theme_action,
            story_id=str(story.get("story_id") or ""),
        )
        if not public
        else ""
    )
    cover = ""
    if photos:
        cover = _photo_card(photos[0], public=public, share_id=share_id,
                            download_enabled=download_enabled, asset_base=asset_base)
        cover = cover.replace('class="tile"', 'class="cover-photo"').replace(' data-photo ', ' data-cover-open ')
        cover = cover.replace('/thumb"', '/preview"').replace('loading="lazy"', 'loading="eager" fetchpriority="high"')
        # A cover is a shortcut, not another member of the photo sequence.
        cover = cover[:cover.rfind('<span>')] + '</button>'
    body = (
        '<main class="shell"><nav class="story-nav" aria-label="Story 탐색">'
        '<span class="story-brand">PhotosMCP</span><div class="story-nav-actions">'
        f'<button type="button" data-all-photos {"disabled" if not photos else ""}>전체 사진</button>'
        '<button type="button" data-info-open>정보와 보기 설정</button></div></nav>'
        '<header class="story-cover"><div class="cover-copy">'
        f'<p class="cover-date">{_e(date_range)}</p><h1>{_story_heading(story.get("title"))}</h1>'
        f'<p class="lede">{_e(story.get("subtitle"))}</p><p class="cover-count">사진 {len(photos)}장</p></div>{cover}</header>'
        '<details class="story-information" data-story-info><summary>정보와 보기 설정</summary><div class="story-information-content">'
        f'<div class="meta"><span>{len(photos)}장</span><span>{_e(date_range)}</span></div>{picker}{overview_html}{people_overview_html}'
        '</div></details><div class="story-content">'
        f'{content}<p class="story-filter-empty" data-filter-empty hidden>이 인물이 포함된 사진이 없습니다.</p></div>'
        f'{closing}{expiry}<footer class="legal"><span>장소·지도 © Google</span>'
        f'<a href="{policy_prefix}/privacy">개인정보 안내</a>'
        f'<a href="{policy_prefix}/terms">이용 안내</a></footer></main>'
        f'{_viewer()}'
    )
    return _page(
        str(story.get("title") or "사진 이야기"),
        body,
        static_base=static_base,
        story_theme=str(visual["theme_id"]),
        design_preset=str(visual["design_preset"]),
    )


def render_owner(
    story: dict[str, Any],
    *,
    presentation: dict[str, Any] | None = None,
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
        date_range = " - ".join(
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
            f'<p>{_e(operation.get("date_from"))} - {_e(operation.get("date_to"))} · {_e(status)}</p>'
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
    story_html = render_story(
        story,
        public=False,
        presentation=presentation,
        theme_action=(
            f'/photos/stories/{_e(story.get("story_id"))}/presentation'
            if story.get("story_id")
            else ""
        ),
    )
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

    async def vendor_asset(request) -> Response:
        return swiper_asset_response(str(request.path_params.get("asset_name") or ""))

    async def favicon(_request) -> Response:
        return story_icon_response()

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
        if kind not in {"thumb", "gallery", "preview", "download"}:
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
            Route("/story-assets/favicon.svg", favicon, methods=["GET"]),
            Route("/story-assets/{asset_name}", vendor_asset, methods=["GET"]),
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
