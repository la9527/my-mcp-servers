# Photos MCP

Photos MCP는 Apple 사진과 로컬 이미지 폴더를 조회·분석·선별하고, 사용자가 승인한 결과를 앨범 또는 디렉토리로 내보내는 macOS AppKit 앱이다. 같은 기능을 `http://127.0.0.1:18791/mcp`의 Streamable HTTP MCP server로 제공한다.

## 주요 기능

- Apple 사진의 앨범·인물·기간 기반 분류
- 앱 내부 폴더 트리에서 로컬 JPEG, PNG, HEIC, SONY ARW 등 선택
- 품질, 장면, 얼굴 신호를 이용한 우수 사진 추천
- 3~6열 반응형 결과 갤러리와 확대 가능한 사진 뷰어
- Apple 사진 앨범과 분류별 로컬 원본 내보내기
- 변경 계획과 승인 토큰을 사용하는 안전한 쓰기
- Linux OpenAI 호환 VLM과 Mac 로컬 MLX 런타임 선택
- Nanobot을 포함한 MCP client 연결

## 빠른 시작

Python 3.12 환경에서:

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -e '.[all,dev]'
./.venv/bin/pytest -q
./.venv/bin/python -m photos_mcp.main
```

설치된 앱이 있으면 다음처럼 시작한다.

```bash
open -a PhotosMcp
curl -fsS http://127.0.0.1:18791/health
```

## MCP 공개 도구

| 도구 | 역할 |
| --- | --- |
| `photos_query` | 상태, 가이드, 탐색, 결과 조회 |
| `photos_select` | 사진 분석과 선별 |
| `photos_write` | 승인된 앨범·파일 쓰기 |
| `photos_workflow` | 여러 단계를 묶은 장기 작업 |

어떤 action을 사용할지 모르면 다음 호출부터 시작한다.

```text
photos_query(action="guide", options={"goal":"overview"})
```

## 앱 빌드

```bash
./scripts/build_framework_standalone.sh
```

스크립트는 `~/Applications/PhotosMcp.app` 설치, codesign 검증, runtime·vendor smoke까지 수행한다.

## 문서

- [문서 전체 인덱스](docs/README.md)
- [설치와 실행](docs/01-getting-started/02-installation.md)
- [사용자 화면](docs/02-user-guide/README.md)
- [MCP 통합](docs/03-integration/README.md)
- [시스템 구조](docs/04-architecture/README.md)
- [운영과 문제 해결](docs/05-operations/04-troubleshooting.md)
- [디자인 시스템](docs/07-design-system/README.md)

문서는 현재 `src/photos_mcp`, `scripts`, `tests`를 기준으로 독립 작성되며 이전 문서는 현행 계약에서 제외한다.

## 검증

```bash
./.venv/bin/python scripts/validate_docs.py
./.venv/bin/pytest -q
```

실제 Apple 사진 권한, iCloud 원본 준비, Linux VLM 연결은 설치본에서 별도로 확인해야 한다.
