"""Read-only HTML presentation for PhotosMcp human-action requests."""

from __future__ import annotations

from html import escape
from typing import Any


def render_user_action_page(payload: dict[str, Any] | None, *, request_id: str) -> tuple[str, int]:
    if payload is None:
        return _page("요청을 찾을 수 없습니다", "이미 정리되었거나 잘못된 링크입니다.", "not_found"), 404
    title = str(payload.get("title") or "사용자 확인이 필요합니다")
    message = str(payload.get("message") or "PhotosMcp에서 작업을 확인해 주세요.")
    status = str(payload.get("status") or "pending")
    expires_at = str(payload.get("expires_at") or "")
    if status in {"completed", "cancelled", "expired"}:
        detail = {
            "completed": "이 요청은 완료되었습니다.",
            "cancelled": "이 요청은 취소되었습니다.",
            "expired": "이 요청은 만료되었습니다. PhotosMcp에서 새 선택을 시작해 주세요.",
        }[status]
    else:
        detail = message
    return _page(title, detail, status, expires_at=expires_at), 200


def _page(title: str, detail: str, status: str, *, expires_at: str = "") -> str:
    safe_title = escape(title[:160])
    safe_detail = escape(detail[:1000])
    safe_status = escape(status[:40])
    safe_expiry = escape(expires_at[:100])
    expiry = f'<p class="meta">만료: {safe_expiry}</p>' if safe_expiry else ""
    instruction = ""
    if status in {"pending", "notified"}:
        instruction = (
            '<section><h2>다음 단계</h2>'
            '<ol><li>Mac에서 PhotosMcp를 엽니다.</li>'
            '<li>Google Photos 사진 선택을 선택합니다.</li>'
            '<li>Chrome에서 범위를 확인하고 Google의 최종 선택을 직접 완료합니다.</li></ol>'
            '<p class="notice">이 화면은 사진을 다운로드하거나 Google 선택을 대신 확정하지 않습니다.</p></section>'
        )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{safe_title}</title>
<style>
:root {{ color-scheme: light dark; font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:#0b1220; color:#e8eef8; }}
main {{ width:min(680px,calc(100% - 32px)); box-sizing:border-box; padding:32px; border:1px solid #2b3a55; border-radius:20px; background:#111b2e; box-shadow:0 20px 60px #0007; }}
.badge {{ display:inline-block; padding:6px 10px; border-radius:999px; background:#203353; color:#b9d5ff; font-size:13px; }}
h1 {{ margin:18px 0 10px; font-size:clamp(25px,5vw,38px); line-height:1.15; }}
h2 {{ margin-top:28px; font-size:18px; }}
p,li {{ color:#c8d4e7; line-height:1.65; }}
.meta {{ font-size:13px; color:#8fa3bf; }}
.notice {{ padding:14px; border-radius:12px; background:#172640; color:#d8e7ff; }}
</style></head><body><main><span class="badge">상태 · {safe_status}</span><h1>{safe_title}</h1><p>{safe_detail}</p>{expiry}{instruction}</main></body></html>"""
