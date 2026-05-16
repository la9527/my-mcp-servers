# photo-source MCP Server

다양한 사진 소스(Apple Photos, Google Photos/Google One, Google Cloud Storage, 로컬 폴더)에 통합 접근할 수 있는 MCP(Model Context Protocol) 서버입니다.
사진 목록 조회, 메타데이터 검색, 썸네일 생성, 키워드 검색, 내보내기 기능을 제공합니다.

## 주요 기능

- **통합 소스 접근** — Apple Photos, Google Photos(Google One), GCS, 로컬 폴더를 동일 인터페이스로 접근
- **사진 목록 조회** — 날짜, 앨범, 인물 필터링 지원
- **메타데이터 조회** — EXIF, 카메라 정보, GPS, 앨범, 인물, 키워드
- **썸네일 생성** — 지정 크기로 리사이즈된 base64 썸네일
- **iCloud 원본 자동 확보** — Apple Photos에서 로컬에 없는 iCloud 사진은 실제 파일이 필요한 시점에 임시 export로 자동 확보
- **키워드 검색** — Apple Photos / Google Photos 검색
- **사진 내보내기** — 원본 또는 리사이즈하여 지정 디렉터리에 내보내기

## 지원 소스

| 소스 | 식별자 | 설명 | 필수 패키지 |
|---|---|---|---|
| 로컬 폴더 | `local` | 파일 시스템의 이미지 파일 접근 | (기본 포함) |
| Apple Photos | `apple` | macOS Apple Photos 라이브러리 접근 | `osxphotos>=0.68` |
| Google Photos | `google` | Google Photos (Google One) 접근 | `google-api-python-client`, `google-auth-oauthlib` |
| Google Cloud Storage | `gcs` | GCS 버킷의 이미지 접근 | `google-cloud-storage>=2.14` |

## 요구 사항

- Python 3.12 이상
- [uv](https://docs.astral.sh/uv/) (권장 패키지 매니저)
- Apple Photos 소스: macOS 필수
- GCS 소스: Google Cloud 인증 설정 필요 (`GOOGLE_APPLICATION_CREDENTIALS` 등)
- Google Photos 소스: OAuth 2.0 Client ID 필요 (Google Cloud Console)

## 설치

```bash
cd mcp-servers/photo-source

# 기본 의존성 설치 (로컬 폴더 소스만)
uv sync

# 모든 소스 포함 설치
uv sync --all-extras

# 또는 필요한 소스만 선택 설치
uv sync --extra apple    # Apple Photos 접근 (osxphotos)
uv sync --extra gcs      # Google Cloud Storage 접근
uv sync --extra google   # Google Photos (Google One) 접근
```

Apple Photos 소스는 macOS 최신 SDK에서 PyObjC 9.x 빌드 이슈를 피하기 위해 `pyobjc` 12.1+ wheel 조합을 함께 사용한다.
이 디렉터리에는 `.python-version` 으로 Python 3.13이 고정되어 있으며, Apple Photos 검증은 이 버전을 기준으로 진행한다.

Apple Photos / iCloud 실검증은 VS Code 통합 터미널보다 `Terminal.app` 에서 더 안정적이다. macOS TCC 권한이 VS Code Electron helper에 붙지 않는 경우가 있어, 필요하면 아래 helper 스크립트로 외부 Terminal.app에서 바로 검증할 수 있다.

```bash
./scripts/validate_icloud_fetch_terminal.sh
./scripts/validate_icloud_fetch_terminal.sh 9C2B2620-2F9F-4DD2-A09E-C798CFD95161
uv run --directory . python ./scripts/validate_icloud_fetch.py 9C2B2620-2F9F-4DD2-A09E-C798CFD95161
```

## 의존성 구성

| 그룹 | 패키지 | 용도 |
|---|---|---|
| 기본 | `mcp>=1.0.0`, `pillow>=10.0`, `pydantic>=2.0` | 핵심 서버, 이미지 처리, 데이터 모델 |
| `apple` | `osxphotos>=0.68` | Apple Photos 라이브러리 접근 |
| `gcs` | `google-cloud-storage>=2.14` | GCS 버킷 접근 |
| `google` | `google-api-python-client>=2.100`, `google-auth-oauthlib>=1.2` | Google Photos 접근 |

## MCP 도구 목록

| 도구 | 설명 | 주요 파라미터 |
|---|---|---|
| `list_photos` | 사진 목록 반환 | `source`, `path_or_bucket?`, `date_from?`, `date_to?`, `album?`, `person?`, `limit?` |
| `get_metadata` | 사진 상세 메타데이터 반환 | `source`, `photo_id`, `path_or_bucket?` |
| `get_thumbnail` | base64 썸네일 반환 | `source`, `photo_id`, `path_or_bucket?`, `max_size?` (기본 512) |
| `search_photos` | 키워드로 사진 검색 (Apple Photos 전용) | `query`, `source?`, `path_or_bucket?`, `limit?` |
| `export_photos` | 사진을 디렉터리에 내보내기 | `source`, `photo_ids`, `output_dir`, `path_or_bucket?`, `max_size?` |

### 도구 상세

#### `list_photos`

사진 목록을 조회합니다. 날짜 범위, 앨범, 인물로 필터링할 수 있습니다.

```json
{
  "source": "local",
  "path_or_bucket": "/Users/me/Pictures",
  "date_from": "2025-01-01",
  "date_to": "2025-12-31",
  "limit": 50
}
```

#### `get_metadata`

사진의 EXIF, 카메라 정보, GPS 좌표, 앨범, 인물, 키워드 등 상세 메타데이터를 반환합니다.

```json
{
  "source": "apple",
  "photo_id": "A1B2C3D4-E5F6-..."
}
```

#### `get_thumbnail`

리사이즈된 사진 썸네일을 base64 문자열로 반환합니다. `photo-ranker` 서버와 함께 사용하여 분석 파이프라인에 이미지를 전달할 수 있습니다.

```json
{
  "source": "local",
  "photo_id": "/Users/me/Pictures/photo.jpg",
  "path_or_bucket": "/Users/me/Pictures",
  "max_size": 512
}
```

#### `search_photos`

Apple Photos 라이브러리에서 키워드로 사진을 검색합니다. 현재 Apple Photos 소스만 지원합니다.

```json
{
  "query": "바다 여행",
  "source": "apple",
  "limit": 30
}
```

#### `export_photos`

선택한 사진을 지정 디렉터리에 내보냅니다. `max_size`를 지정하면 리사이즈된 버전으로 내보냅니다.

```json
{
  "source": "local",
  "photo_ids": ["/path/to/photo1.jpg", "/path/to/photo2.jpg"],
  "output_dir": "/tmp/exported",
  "path_or_bucket": "/Users/me/Pictures",
  "max_size": 1024
}
```

## 디렉터리 구조

```
photo-source/
├── server.py           # MCP 서버 엔트리포인트 (5개 도구)
├── models.py           # 데이터 모델 (Photo, PhotoMetadata, ExportResult)
├── sources/
│   ├── local_folder.py # 로컬 파일 시스템 소스
│   ├── apple_photos.py # Apple Photos 소스 (osxphotos)
│   └── gcs.py          # Google Cloud Storage 소스
├── pyproject.toml
└── tests/              # 44개 테스트
```

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
uv run --directory /path/to/mcp-servers/photo-source server.py
```

### OpenClaw

`openclaw.json` 또는 OpenClaw 설정 파일에 추가:

```json
{
  "mcpServers": {
    "photo-source": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-servers/photo-source", "server.py"]
    }
  }
}
```

### Claude Desktop

`claude_desktop_config.json`에 추가:

```json
{
  "mcpServers": {
    "photo-source": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-servers/photo-source", "server.py"]
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
    "photo-source": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-servers/photo-source", "server.py"]
    }
  }
}
```

### VS Code (GitHub Copilot)

`.vscode/mcp.json`에 추가:

```json
{
  "servers": {
    "photo-source": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-servers/photo-source", "server.py"]
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
| 인자 | `run --directory /path/to/mcp-servers/photo-source server.py` |
| 프로토콜 | MCP (Model Context Protocol) |

### photo-ranker와 함께 사용하기

`photo-source`로 사진을 조회/검색한 뒤 `photo-ranker`로 분석하는 워크플로우를 구성할 수 있습니다.
두 서버를 모두 MCP 클라이언트에 등록하면, LLM이 자동으로 적절한 도구를 선택하여 호출합니다.

```json
{
  "mcpServers": {
    "photo-source": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-servers/photo-source", "server.py"]
    },
    "photo-ranker": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-servers/photo-ranker", "server.py"]
    }
  }
}
```

**사용 예시 워크플로우:**

1. `list_photos`로 특정 기간의 사진 목록 조회
2. `get_thumbnail`로 각 사진의 썸네일(base64) 획득
3. `score_quality`로 품질 점수 산정
4. `rank_best_shots`로 최종 베스트 샷 선별
5. `export_photos`로 선별된 사진 내보내기

### 소스별 사전 설정

- **로컬 폴더**: 추가 설정 없이 바로 사용 가능
- **Apple Photos**: macOS에서 `uv sync --extra apple`로 osxphotos 설치. Photos 앱 접근 권한 허용 필요
- **Apple Photos / iCloud**: 로컬에 없는 사진은 `get_thumbnail` 또는 실제 파일이 필요한 처리 시점에 osxphotos의 missing export 경로로 자동 확보한다. 첫 접근은 느릴 수 있고, Photos 접근 권한과 네트워크 연결이 필요하다.
- **GCS**: `uv sync --extra gcs` 설치 후 `GOOGLE_APPLICATION_CREDENTIALS` 환경변수 또는 `gcloud auth application-default login`으로 인증 설정

## 라이선스

MIT
