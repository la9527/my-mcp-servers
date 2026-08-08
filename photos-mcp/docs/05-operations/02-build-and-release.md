# 빌드와 배포

> 공식 빌드 경로: `scripts/build_framework_standalone.sh`

standalone 앱은 Python framework, 의존성, vendor 코드를 py2app bundle에 포함하고 ad-hoc 서명과 세 단계 smoke를 통과한 뒤 설치된다.

## 개발 환경 준비

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -e '.[all,dev]'
./.venv/bin/pytest -q
```

macOS framework Python의 위치가 자동 검색되지 않으면 `PHOTOS_MCP_FRAMEWORK_RUNTIME_DIR`을 지정한다. 빌드용 site-packages는 `.venv-framework312` 또는 `.venv`에서 찾는다.

## 앱 빌드

```bash
./scripts/build_framework_standalone.sh
```

기본 산출물과 설치 위치는 다음과 같다.

| 항목 | 경로 |
| --- | --- |
| 빌드 bundle | `dist-framework-standalone/PhotosMcp.app` |
| 설치 bundle | `~/Applications/PhotosMcp.app` |
| 공용 링크 | `/Applications/PhotosMcp.app` |

`/Applications/PhotosMcp.app`에 심볼릭 링크가 아닌 앱이 이미 있으면 빌드 스크립트는 덮어쓰지 않는다.

## 빌드가 자동 검증하는 항목

```mermaid
flowchart LR
    A["아이콘 생성"] --> B["py2app bundle"]
    B --> C["dylib 보정"]
    C --> D["깊이 우선 ad-hoc 서명"]
    D --> E["codesign 검증"]
    E --> F["--health"]
    F --> G["--runtime-import-smoke"]
    G --> H["--vendor-runtime-smoke"]
    H --> I["설치본 복사·재검증"]
```

smoke 명령은 사진을 수정하지 않는다.

```bash
~/Applications/PhotosMcp.app/Contents/MacOS/PhotosMcp --health
~/Applications/PhotosMcp.app/Contents/MacOS/PhotosMcp --runtime-import-smoke
~/Applications/PhotosMcp.app/Contents/MacOS/PhotosMcp --vendor-runtime-smoke
codesign --verify --deep --strict ~/Applications/PhotosMcp.app
```

## 주요 빌드 override

| 변수 | 목적 |
| --- | --- |
| `PHOTOS_MCP_FRAMEWORK_VERSION` | framework Python 버전, 기본 `3.12` |
| `PHOTOS_MCP_FRAMEWORK_RUNTIME_DIR` | Python.framework 검색 경로 |
| `PHOTOS_MCP_SITE_PACKAGES_DIR` | 빌드 의존성 경로 |
| `PHOTOS_MCP_DIST_DIR` | bundle 산출 디렉토리 |
| `PHOTOS_MCP_BUILD_DIR` | py2app 임시 빌드 디렉토리 |
| `PHOTOS_MCP_INSTALL_BUNDLE_PATH` | 설치 위치 |
| `PHOTOS_MCP_PUBLIC_APPLICATION_LINK` | 공용 심볼릭 링크 위치 |
| `PHOTOS_MCP_ICON_PYTHON` | 아이콘 생성 Python |

## 배포 전 체크리스트

1. 전체 pytest가 통과한다.
2. standalone 빌드 스크립트가 끝까지 통과한다.
3. 기존 실행 앱을 종료하고 새 설치본을 실행한다.
4. `/health`와 `/health/capabilities`를 확인한다.
5. Apple 사진 읽기, 로컬 폴더 RAW 미리보기, 키보드 선택을 확인한다.
6. 테스트 앨범 또는 임시 디렉토리로 승인 기반 내보내기를 한 번 검증한다.
