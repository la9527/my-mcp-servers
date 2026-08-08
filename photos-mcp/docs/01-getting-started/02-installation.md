# 설치와 실행

> 대상: 로컬 개발 및 설치 앱 운영자
>
> 근거: `pyproject.toml`, `PhotosMcp.py`, `scripts/build_framework_standalone.sh`

## 요구 환경

- macOS
- Apple 사진 접근 권한
- Python 3.12 기반 개발 환경
- standalone 앱 빌드 시 서명 가능한 macOS 도구 체인

선택 기능은 별도 dependency group을 사용한다. 기본 실행에는 `mcp`, `osxphotos`, Pillow, PyObjC 런타임과 vendor 패키지가 필요하고, VLM·얼굴·Google/GCS 기능은 해당 extra를 선택한다.

## 개발 환경

저장소 루트의 기존 가상환경을 사용하는 경우:

```bash
cd /Volumes/ExtData/my-mcp-servers/photos-mcp
.venv/bin/python -m photos_mcp.main --version
.venv/bin/python -m photos_mcp.main --health
```

의존성을 새로 구성하는 경우 프로젝트의 `pyproject.toml`과 lockfile을 기준으로 설치한다.

```bash
uv sync --extra dev --extra app
```

## standalone 앱 빌드

```bash
./scripts/build_framework_standalone.sh
```

기본 설치 대상은 다음과 같다.

- 실제 앱: `~/Applications/PhotosMcp.app`
- 공용 링크: `/Applications/PhotosMcp.app`

별도 위치에 설치하려면 환경변수를 지정한다.

```bash
PHOTOS_MCP_INSTALL_BUNDLE_PATH=/Volumes/ExtData/system/Applications/PhotosMcp.app \
PHOTOS_MCP_PUBLIC_APPLICATION_LINK=/Applications/PhotosMcp.app \
./scripts/build_framework_standalone.sh
```

빌드 스크립트는 framework runtime 준비, dependency 복사, py2app 번들 생성, native library 서명, import smoke, 설치본 검증을 순서대로 수행한다.

## 실행

```bash
open -a PhotosMcp
```

또는 정확한 번들 경로를 지정한다.

```bash
open /Volumes/ExtData/system/Applications/PhotosMcp.app
```

앱은 single-instance lock을 사용한다. 이미 실행 중이면 두 번째 인스턴스는 종료 코드 `75`로 거부된다.

## 설치 확인

```bash
curl -fsS http://127.0.0.1:18791/health
curl -fsS http://127.0.0.1:18791/health/capabilities
codesign --verify --deep --strict /Applications/PhotosMcp.app
```

CLI smoke는 사진을 변경하지 않는다.

```bash
/Applications/PhotosMcp.app/Contents/MacOS/PhotosMcp --runtime-import-smoke
/Applications/PhotosMcp.app/Contents/MacOS/PhotosMcp --vendor-runtime-smoke
```

## 권한

첫 실행에서 Apple 사진 접근 요청이 나타나면 허용한다. 앨범 쓰기 또는 자동화 검사는 실제로 해당 기능을 사용할 때 별도로 수행될 수 있다. 권한 상태는 앱의 `환경 및 권한` 화면과 capabilities endpoint에서 확인한다.

## 다음 단계

[첫 실행](03-first-run.md)에서 Apple 사진과 로컬 폴더의 기본 분류 흐름을 확인한다.
