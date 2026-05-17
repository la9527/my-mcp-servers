# photo-ranker MCP Server

사진 품질 분석, 분류, 랭킹을 수행하는 MCP(Model Context Protocol) 서버입니다.
VLM(Vision Language Model), CLIP 기반 미적 평가, 얼굴 인식, 중복 감지 엔진을 결합하여 대량의 사진을 자동으로 분류하고 최적의 사진을 선별합니다.

## 주요 기능

- **품질 점수 산정** — 5-component 기술적 품질 분석 (blur, exposure, noise, resolution, color diversity)
- **VLM 기반 장면 묘사** — Qwen2.5-VL 모델을 사용한 자연어 장면 설명
- **이벤트 자동 분류** — 여행, 가족 모임, 음식, 풍경 등 이벤트 타입 자동 분류
- **얼굴 인식** — insightface / mediapipe / face-recognition 3-tier 백엔드, 소형 이미지 upscale retry
- **중복 감지** — perceptual hash 기반 유사 사진 그룹핑
- **LAION Aesthetic** — sigmoid 매핑으로 3-7 구간 변별력 확대
- **베스트 샷 랭킹** — 다차원 점수를 종합하여 최고의 사진 선별
- **선택 프로필** — `general`, `person`, `landscape` 프로필별로 인물 중심/풍경 중심 선별 지원
- **Apple Photos 연동** — osxphotos 읽기 + photoscript 앨범 쓰기
- **iCloud 원본 자동 확보** — Apple Photos에서 로컬에 없는 사진도 분류 시점에 자동 export로 확보
- **백그라운드 Job 시스템** — 대량 분류 작업을 비동기 Job으로 관리
- **SQLite 영속성** — Job 상태와 분류 결과를 DB에 저장
- **검토 WebUI** — preview, 얼굴 crop, selected/tag/note 편집용 로컬 리뷰 UI
- **배치 CLI** — 커맨드라인에서 직접 분류 작업 실행

## 요구 사항

- Python 3.12 이상
- [uv](https://docs.astral.sh/uv/) (권장 패키지 매니저)

## 설치

```bash
cd mcp-servers/photo-ranker

# 기본 의존성 설치
uv sync

# 모든 엔진 포함 설치
uv sync --all-extras

# 또는 필요한 엔진만 선택 설치
uv sync --extra vlm          # VLM 장면 묘사 (mlx-vlm, Apple Silicon 전용)
uv sync --extra aesthetic     # CLIP 미적 평가 (open-clip-torch + torch)
uv sync --extra face          # 얼굴 인식 (face-recognition)
```

> **참고:** VLM 엔진(`mlx-vlm`)은 Apple Silicon Mac에서만 동작합니다. 다른 플랫폼에서는 VLM 없이도 나머지 엔진이 정상 동작합니다.

## 의존성 구성

| 그룹 | 패키지 | 용도 |
|---|---|---|
| 기본 | `mcp>=1.0.0`, `pillow>=10.0`, `numpy>=1.26`, `pydantic>=2.0`, `imagehash>=4.3` | 핵심 서버, 이미지 처리, 해시 |
| `vlm` | `mlx-vlm>=0.1` | VLM 장면 묘사 (Apple Silicon) |
| `aesthetic` | `open-clip-torch>=2.24`, `torch>=2.0` | CLIP 미적 품질 평가 |
| face          | `insightface>=0.7`, `mediapipe>=0.10`, `face-recognition>=1.3` | 얼굴 감지 (3-tier) |

## MCP 도구 목록

### 분석 도구 (6개)

| 도구 | 설명 | 주요 파라미터 |
|---|---|---|
| `score_quality` | 사진의 기술적 품질 점수 산정 | `image_b64` (base64 이미지), `photo_id?` |
| `detect_faces` | 사진 속 얼굴 감지 | `image_b64` |
| `describe_scene` | VLM으로 장면을 자연어로 묘사 | `image_b64`, `prompt?` |
| `classify_event` | 이벤트 타입 자동 분류 | `image_b64` |
| `find_duplicates` | 해시 기반 중복 사진 그룹핑 | `photo_hashes_json` (해시 딕셔너리), `threshold?` |
| `rank_best_shots` | 종합 점수로 베스트 샷 랭킹 | `photo_scores_json` (점수 배열), `top_n?`, `selection_profile?` |

### Job 관리 도구 (8개)

| 도구 | 설명 | 주요 파라미터 |
|---|---|---|
| `start_classify_job` | 백그라운드 분류 작업 시작 | `source` ("local"/"apple"/"gcs"), `source_path`, `selection_profile?` |
| `get_job_status` | 작업 상태 조회 | `job_id` |
| `get_job_summary` | 진행률/선택 프로필/선택 개수 포함 요약 조회 | `job_id` |
| `get_job_result` | 완료된 작업의 랭킹 결과 조회 | `job_id`, `top_n?` (기본 20) |
| `cancel_job` | 실행/대기 중인 작업 취소 | `job_id` |
| `delete_job` | 완료/실패/취소된 단일 작업 기록 삭제 | `job_id` |
| `clear_job_history` | terminal 작업 기록 일괄 삭제 | `status?` (`completed`/`failed`/`cancelled`, 빈 값이면 전체 terminal) |
| `list_jobs` | 작업 목록 조회 | `status?` ("pending"/"running"/"completed"/"failed"/"cancelled") |

`delete_job` 과 `clear_job_history` 는 terminal job 에만 적용한다. 실행 중이거나 대기 중인 job 은 먼저 `cancel_job` 으로 취소해야 하며, 삭제 시 SQLite `jobs`, `photo_results`, `job_assets`, `face_reviews`, `stage_checkpoints` 와 in-memory queue 기록을 함께 정리한다.

### 인물 관리 도구 (4개)

| 도구 | 설명 | 주요 파라미터 |
|---|---|---|
| `register_face` | 이미지에서 얼굴을 감지하여 known person 등록 | `image_b64`, `name` |
| `register_face_from_job` | 이전 분류 결과의 캐시된 임베딩으로 등록 | `photo_id`, `face_idx`, `name` |
| `list_known_faces` | 등록된 인물 목록 조회 | — |
| `delete_known_face` | 등록된 인물의 모든 임베딩 삭제 | `name` |

### Apple Photos 앨범 도구 (6개)

| 도구 | 설명 | 주요 파라미터 |
|---|---|---|
| `create_album` | Apple Photos에 앨범 생성 | `name`, `folder?` |
| `add_to_album` | 기존 라이브러리 사진을 앨범에 추가 (복제 없음) | `photo_uuids_json`, `album_name`, `folder?` |
| `organize_results` | 분류 결과를 이벤트별 앨범으로 자동 정리 | `job_id`, `album_prefix?`, `min_score?` |
| `import_photos` | 외부 사진을 Photos 라이브러리에 가져오기 | `photo_paths_json`, `album_name?` |
| `import_and_organize` | 외부 사진 가져오기 + 분류별 앨범 정리 | `photo_paths_json`, `results_json` |
| `list_photo_albums` | Apple Photos 앨범 목록 조회 | — |

### 검토/선택 도구 (5개)

| 도구 | 설명 | 주요 파라미터 |
|---|---|---|
| `get_review_items` | preview/selected/tags/note 포함 검토 목록 | `job_id`, `top_n?`, `selected_only?` |
| `set_photo_review` | selected/tags/note 저장 | `job_id`, `photo_id`, `tags_json?`, `selected?`, `note?` |
| `list_photo_faces` | 얼굴 crop/bbox/속성 조회 | `job_id`, `photo_id` |
| `label_face_in_job` | 얼굴 이름 지정 및 known face 등록 | `job_id`, `photo_id`, `face_idx`, `name` |
| `export_selected_photos` | selected=true 사진만 디렉터리로 export | `job_id`, `output_dir`, `group_by_date?`, `mode?` |

### E2E 워크플로우 도구 (1개)

| 도구 | 설명 | 주요 파라미터 |
|---|---|---|
| `classify_and_organize` | 소스 → 분류 → 앨범 정리 전체 워크플로우 | `source`, `source_path`, `album_prefix?`, `limit?`, `selection_profile?` |
| `curate_best_photos` | 최신/필터된 사진에서 상위 퍼센트를 고르고 review 또는 단일 앨범에 반영 | `source`, `limit?`, `quality_top_percent?`, `writeback_mode?`, `target_album_name?`, `selection_profile?` |

## 점수 체계

최종 랭킹은 4가지 차원의 가중 합산으로 결정됩니다:

| 항목 | 가중치 | 설명 |
|---|---|---|
| 품질 (quality) | 0.30 | blur, exposure, noise, resolution, color + sigmoid aesthetic |
| 가족 (family) | 0.25 | 얼굴 수, 알려진 인물, 표정 |
| 이벤트 (event) | 0.25 | 이벤트 분류 신뢰도 |
| 고유성 (uniqueness) | 0.20 | 중복이 적을수록 높은 점수 |

선택 프로필별 동작:

- `general` — 기존 균형형. `curate_best_photos` 는 `quality_score` 기준 상위 퍼센트를 유지
- `person` — 얼굴 수, known person, portrait/event 맥락을 더 강하게 반영
- `landscape` — `OUTDOOR`/`TRAVEL`, 장면 설명의 풍경 키워드, low-face 컷을 우대

## 2단계 파이프라인

대량 분류 시 효율을 위해 2단계 파이프라인으로 동작합니다:

1. **Stage 1 (필터)** — 품질 점수 + 얼굴 감지 + 중복 검사 (~180ms/장)
2. **Stage 2 (VLM)** — 상위 사진만 VLM 정밀 분석 (~5s/장)

## 배치 CLI

```bash
# 기본 실행
uv run batch_classify.py --source local --path /photos/2025

# 옵션 지정
uv run batch_classify.py \
  --source local \
  --path /photos \
  --min-quality 15 \
  --vlm-top-n 10 \
  --limit 500 \
  --output results.json
```

## 디렉터리 구조

```
photo-ranker/
├── server.py           # MCP 서버 엔트리포인트 (22개 도구)
├── engines/
│   ├── vlm.py          # Qwen2.5-VL 장면 묘사 엔진
│   ├── aesthetic.py    # CLIP + LAION + 5-component 기술 품질 엔진
│   ├── face.py         # 3-tier 얼굴 인식 (insightface/mediapipe/face-recognition)
│   ├── dedup.py        # perceptual hash 중복 감지 엔진
│   └── exif.py         # EXIF 메타데이터 + GPS + 방향 보정
├── models.py           # 데이터 모델 (EventType, QualityScore, …)
├── scoring.py          # 가중 점수 산정 로직
├── jobs.py             # 비동기 Job 큐
├── pipeline.py         # 2단계 분류 파이프라인
├── album_writer.py     # Apple Photos 앨범 쓰기 (photoscript)
├── db.py               # SQLite 영속성 (WAL 모드)
├── batch_classify.py   # 배치 CLI
├── review_app.py       # 로컬 검토 WebUI + artifact HTTP 서버
├── artifacts.py        # preview/face crop artifact 저장
├── local_writer.py     # 로컬 디렉터리 write-back
├── pyproject.toml
└── tests/              # 213개+ 테스트
```

## Review WebUI

```bash
cd mcp-servers/photo-ranker
uv sync --extra review --extra dev
uv run review_app.py
```

이후 브라우저에서 아래 주소로 접속합니다.

```text
http://127.0.0.1:8765/review/<job_id>
```

이 화면에서 아래 작업을 수행할 수 있습니다.

- preview 이미지 검토
- selected 토글
- tags / note 편집
- 얼굴 crop 확인
- 얼굴 이름 지정 및 known face 등록
- selected-only export 실행

## 추천 호출 예시

Apple Photos 최신 30장 중 quality 상위 30%만 review-selected 처리:

```text
curate_best_photos(
  source="apple",
  limit=30,
  quality_top_percent=30,
  writeback_mode="review"
)
```

Apple Photos 최신 30장 중 quality 상위 30%만 `잘나온사진1` 앨범에 추가:

```text
curate_best_photos(
  source="apple",
  limit=30,
  quality_top_percent=30,
  writeback_mode="album",
  target_album_name="잘나온사진1"
)
```

Apple Photos 최신 30장을 풍경 중심으로 다시 랭킹하고 상위 30%만 고르기:

```text
curate_best_photos(
  source="apple",
  limit=30,
  quality_top_percent=30,
  selection_profile="landscape",
  writeback_mode="review"
)
```

백그라운드 job 을 인물 중심으로 실행:

```text
start_classify_job(
  source="apple",
  source_path="가족",
  selection_profile="person",
  limit=120
)
```

## Apple Photos iCloud 처리 방식

- Apple Photos 자산에 로컬 원본 경로가 있으면 그 파일을 바로 사용한다.
- 로컬 원본이 없고 iCloud에만 있는 경우, 분류 시점에 osxphotos의 missing export 경로로 임시 파일을 자동 확보한다.
- 이 동작은 첫 접근 시 느릴 수 있으며, 인터넷 연결과 Photos 접근 권한이 필요하다.
- 확보된 임시 파일 경로는 해당 작업의 `source_photo_path`로 저장되어 review/export 후속 흐름에서도 재사용된다.
- VS Code 통합 터미널에서 Photos 권한이 붙지 않는 경우가 있어, 실 iCloud fetch 검증은 `Terminal.app` 에서 `./scripts/validate_icloud_fetch_terminal.sh [UUID]` 로 실행하는 편이 안정적이다.
- ZeroClaw나 launchd에서 `photo-ranker`를 붙일 때 Apple Photos 앨범 쓰기 권한이 안 붙으면 `PHOTO_RANKER_APPLE_EVENTS_MODE=terminal` 로 Terminal helper 경로를 켜는 편이 안정적이다. 이 모드에서는 album/list/create/add/import 계열 작업을 Terminal.app 안의 별도 Python helper로 실행한다.
- Apple Photos source fetch 에서도 macOS 접근 허용 프롬프트를 줄이려면 `PHOTO_RANKER_APPLE_FETCH_MODE=terminal` 을 함께 켜는 편이 좋다. 그러면 iCloud missing export 도 Terminal.app 안의 helper 로 우회되어, launchd/VS Code/Python 프로세스마다 별도로 Photos 권한을 다시 묻는 상황을 줄일 수 있다.
- 그래도 macOS 접근 허용 프롬프트를 코드만으로 완전히 없애지는 못한다. 프롬프트는 TCC(Privacy/Automation) 정책이 결정하므로, 서로 다른 caller app/프로세스(예: VS Code 통합 터미널, launchd Python, Terminal.app)가 Photos 또는 Terminal.app 에 Apple Events 를 보내면 각각 한 번씩 허용이 필요할 수 있다. 현재 구조에서 반복 프롬프트를 줄이는 현실적인 방법은 caller 를 `Terminal.app` helper 로 최대한 통일하는 것이다.
- 프롬프트를 구조적으로 더 줄이려면 ad-hoc Python 프로세스 대신 서명된 단일 helper app 으로 Apple Events 를 보내도록 패키징하고, `NSAppleEventsUsageDescription` / automation entitlement 를 갖춘 앱으로 고정해야 한다.
- 현재 터미널 세션에서 바로 보고 싶으면 `uv run --directory . python ./scripts/validate_icloud_fetch.py [UUID]` 를 사용할 수 있다.

## 테스트

```bash
# 전체 테스트
uv run pytest

# 커버리지 포함
uv run pytest --cov=. --cov-report=term-missing
```

---

## MCP 클라이언트 연동 가이드

이 서버는 [MCP(Model Context Protocol)](https://modelcontextprotocol.io/) 표준을 따르며, stdio 전송을 사용합니다. MCP를 지원하는 모든 클라이언트에서 사용할 수 있습니다.

### 서버 실행 명령

```bash
uv run --directory /path/to/mcp-servers/photo-ranker server.py
```

### OpenClaw

`openclaw.json` 또는 OpenClaw 설정 파일에 추가:

```json
{
  "mcpServers": {
    "photo-ranker": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-servers/photo-ranker", "server.py"]
    }
  }
}
```

### Claude Desktop

`claude_desktop_config.json`에 추가:

```json
{
  "mcpServers": {
    "photo-ranker": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-servers/photo-ranker", "server.py"]
    }
  }
}
```

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

### Cursor

Cursor 설정 (`Settings > MCP Servers`)에서 추가하거나, `.cursor/mcp.json`에 작성:

```json
{
  "mcpServers": {
    "photo-ranker": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-servers/photo-ranker", "server.py"]
    }
  }
}
```

### VS Code (GitHub Copilot)

`.vscode/mcp.json`에 추가:

```json
{
  "servers": {
    "photo-ranker": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-servers/photo-ranker", "server.py"]
    }
  }
}
```

### 기타 MCP 클라이언트

MCP stdio 전송을 지원하는 모든 클라이언트에서 아래 정보로 연결할 수 있습니다:

| 항목 | 값 |
|---|---|
| 전송 방식 | stdio |
| 명령어 | `uv` |
| 인자 | `run --directory /path/to/mcp-servers/photo-ranker server.py` |
| 프로토콜 | MCP (Model Context Protocol) |

### 옵셔널 엔진별 설치 참고

MCP 클라이언트에서 모든 도구를 사용하려면 필요한 엔진 의존성을 사전에 설치해야 합니다.
엔진이 미설치된 경우에도 서버는 정상 시작되며, 해당 엔진이 필요한 도구 호출 시 graceful 에러 메시지를 반환합니다.

```bash
# 모든 엔진 설치 (권장)
cd /path/to/mcp-servers/photo-ranker && uv sync --all-extras

# 또는 경량 설치 (VLM/CLIP 없이 기본 분석만)
cd /path/to/mcp-servers/photo-ranker && uv sync
```

### `openai_compat` fallback smoke 메모

- `PHOTO_RANKER_VLM_BACKEND=openai_compat` 경로에서 local endpoint 를 primary 로 두고 external fallback 을 검증할 때, stdio MCP client 가 서버 subprocess 를 직접 띄우면 `PHOTO_RANKER_VLM_*` 환경변수를 명시적으로 넘겨야 한다. 부모 셸에서 inline 으로만 준 값은 subprocess 에 자동 상속되지 않을 수 있다.
- 실제 VLM code path 는 입력 이미지를 `64px` 로 줄이지 않는다. `engines/vlm.py` 는 긴 변 기준으로 최대 `512px` 까지만 downscale 한다.
- 너무 작은 smoke fixture 는 hosted provider 에서 `image_parse_error` 로 거절될 수 있다. external fallback 검증에는 `64x64` 이상, 가능하면 일반적인 JPEG fixture 를 사용하는 편이 안정적이다.

반복 검증용 read-only smoke script:

```bash
cd /path/to/mcp-servers/photo-ranker
source ~/.nanobot/nanobot.env
uv run --python 3.12 python scripts/validate_mcp_openai_compat_fallback.py \
  --primary-api-base http://127.0.0.1:1248/v1 \
  --primary-model mlx-community/Qwen3.5-27B-4bit \
  --fallback-model gpt-5.4-mini-2026-03-17
```

이 스크립트는 기본적으로 `512x512` JPEG fixture 를 생성하고, primary local endpoint vision probe 결과와 MCP `describe_scene` JSON payload 를 함께 출력한다.

## 라이선스

MIT
