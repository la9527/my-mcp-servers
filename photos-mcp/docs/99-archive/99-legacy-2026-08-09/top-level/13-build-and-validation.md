# photos-mcp build and validation

이 문서는 `photos-mcp` 의 packaging, standalone bundle build, import smoke, 테스트 검증 흐름을 소스 기준으로 정리한다. 목표는 build contract 와 validation gate 를 코드와 같은 언어로 설명하는 것이다.

## 1. dependency contract

기준 파일:

- `pyproject.toml`
- `src/photos_mcp/packaging_contract.py`
- `src/photos_mcp/packaging.py`

### 1.1 base dependency

기본 install 은 아래 런타임을 포함한다.

- `mcp`
- `numpy`
- `imagehash`
- `osxphotos`
- `pillow`, `pillow-heif`
- `pydantic`
- `uvicorn`

### 1.2 optional extras

주요 extras:

- `app`: py2app build
- `apple`: `photoscript`, `wurlitzer`
- `gcs`: Google Cloud Storage
- `google`: Google Photos 계열 API
- `vlm`: HTTP client + `mlx-vlm`
- `aesthetic`: CLIP / torch 계열
- `face`, `face-legacy`: face engine 계열
- `review`: FastAPI review app 계열
- `dev`: pytest, pytest-asyncio

의미:

- source test 와 wheel metadata 는 `pyproject.toml` 이 source of truth 다.
- bundle build 는 이 위에 `packaging_contract.py` 의 py2app include/resource allowlist 가 추가로 얹힌다.

## 2. py2app packaging contract

`packaging_contract.py` 는 py2app 에 직접 주는 포함 계약을 고정한다.

### 2.1 app package 목록

- `APP_PACKAGES = ["photos_mcp", "apple_terminal_helper"]`

wheel/setup 기준으로 repo 안에서 실제 패키징하는 Python package 는 이 두 개다.

### 2.2 py2app packages / includes

- `PY2APP_PACKAGES`: py2app 이 package 형태로 실어야 하는 runtime package
- `PY2APP_INCLUDES`: dynamic import 성격이라 별도 include 가 필요한 module
- `PY2APP_EXCLUDES`: tkinter 계열 등 bundle 에 넣지 않을 것

중요 예시:

- `mcp.server.fastmcp`
- `anyio._backends._asyncio`
- `uvicorn.protocols.http.h11_impl`
- `uvicorn.loops.asyncio`
- `uvicorn.lifespan.on`

이 목록은 source import tree 만으로는 py2app 이 놓치기 쉬운 runtime dependency 를 보완한다.

### 2.3 site-packages resource allowlist

`SITE_PACKAGES_RESOURCE_NAMES`, `PREFIXES`, `SUFFIXES` 는 bundle resource 로 복사할 site-packages child 를 제한한다.

의미:

- 전체 `site-packages` 복사가 아니다.
- 실제 필요한 package, dist-info, native helper 파일만 allowlist 로 넣는다.
- `__pycache__` 는 제외한다.

## 3. resource staging 로직

`packaging.py` 는 py2app 호출 전에 resource staging 을 수행한다.

### 3.1 vendor staging

vendored runtime 두 개를 staged resource 로 복사한다.

- `src/photos_mcp/vendor/photo-source`
- `src/photos_mcp/vendor/photo-ranker`

복사 목적지는 bundle 안 `lib/photos_mcp/vendor` 다.

### 3.2 ignored resources

복사 시 아래는 제외된다.

- `.venv`
- `.git*`
- `.pytest_cache`, `.mypy_cache`, `.ruff_cache`
- `__pycache__`
- `pyproject.toml`, `uv.lock`
- `tests`

의미:

- build 산출물에는 runtime 에 불필요한 개발 메타와 test data 를 넣지 않는다.
- source tree 안의 embedded venv 나 stale build 산출물 누적을 줄인다.

### 3.3 stale stage cleanup

build 전에 아래 stale root 를 지운다.

- `src/build`
- `build/py2app-resources/legacy-vendor-stage`

이건 이전 packaging 시도의 남은 리소스가 새 bundle 에 섞이는 것을 막기 위한 cleanup 이다.

### 3.4 canonical bundle name

py2app 가 `photos-mcp.app` 로 만들더라도 최종 이름은 `PhotosMcp.app` 으로 정규화한다.

## 4. framework standalone build script

기준 파일:

- `scripts/build_framework_standalone.sh`

이 스크립트는 framework Python 기반 standalone bundle build 전체 흐름을 책임진다.

### 4.1 runtime discovery 순서

framework runtime 탐색 순서:

1. `PHOTOS_MCP_FRAMEWORK_RUNTIME_DIR`
2. repo 로컬 `.framework-python-runtime`
3. `/Library/Frameworks`
4. Homebrew `python@3.12` framework cellar

site-packages 탐색 순서:

1. `PHOTOS_MCP_SITE_PACKAGES_DIR`
2. `.venv-framework312/.../site-packages`
3. `.venv/.../site-packages`

### 4.2 build 단계

1. app icon 생성
2. 기존 `dist-framework-standalone`, `build-framework-standalone` 삭제
3. framework Python / site-packages / DYLD path env 설정
4. `PHOTOS_MCP_SKIP_PY2APP_CODESIGN=1` 로 py2app 내부 ad-hoc signing 일단 생략
5. `setup.py py2app` 실행
6. 결과 bundle 이름 정규화
7. embedded `liblzma.5.dylib` 문제 있으면 clean copy 로 교체
8. depth-first ad-hoc signing 재적용
9. `codesign --verify --deep --strict`

ad-hoc 설치본은 기본 CDHash requirement 대신 `com.nanobot.photos-mcp` 번들 식별자를 사용하는 명시적 designated requirement를 마지막 서명 단계에 추가한다. 이를 통해 소스가 바뀐 재빌드에서도 macOS TCC가 동일한 PhotosMcp 앱으로 판별할 수 있는 일관된 식별 요구사항을 유지한다. 필요하면 `PHOTOS_MCP_DESIGNATED_REQUIREMENT`로 덮어쓸 수 있다.

21,000장 이상 보관함의 최초 metadata DB 로드는 10초를 넘을 수 있으므로 사진 보관함 읽기 검사는 별도 30초 timeout을 사용한다. `PHOTOS_MCP_LIBRARY_PREFLIGHT_TIMEOUT_SECONDS`로 환경에 맞게 조정할 수 있으며, 선택 검사 timeout과는 분리한다. 실제 `photos_query(action="list"|"ready_only")`도 별도 30초 제한을 사용하며, 초기 색인 로딩이 더 길어지면 연결을 붙잡지 않고 `status="warning"`, `error_code="library_list_timeout"`, `can_retry=true`를 반환한다. 이 제한은 `PHOTOS_MCP_LIBRARY_LIST_TIMEOUT_SECONDS`로 조정한다.

`photos-mcp-live-validate --include-workflows`의 local workflow는 사용자 사진을 복사하지 않고 프로젝트의 공개 미리보기 PNG를 임시 폴더에 복사해 실행한다. 배포본에 해당 asset이 없을 때만 자체 생성 PNG로 대체한다. Linux 원격 VLM을 쓰는 첫 요청은 PC 기동, SSH 터널, 모델 로딩을 포함할 수 있으므로 workflow 완료 대기는 최소 120초로 둔다. 이는 분석 요청의 서비스 타임아웃이 아니라 검증 도구의 관측 창이다.

이 live workflow는 분류, 결과 조회, local directory organize, review 작업까지만 실행한다. `import_to_album`은 비어 있지 않은 경로 목록이 필요하고 실제 Photos 보관함을 변경하므로 자동 실행하지 않는다. import write-back은 명시적인 운영 승인 아래 별도 검증한다.
10. `PYTHONDONTWRITEBYTECODE=1`로 built bundle `--health` 실행 후 다시 서명 검증
11. 기본 install path인 `~/Applications/PhotosMcp.app`에 복사 후 같은 방식으로 verify + `--health` + final verify
12. `/Applications/PhotosMcp.app` 심볼릭 링크를 설치본으로 연결해 Finder와 `open -a PhotosMcp` 실행 경로를 제공

### 4.3 중요한 env

- `PHOTOS_MCP_FRAMEWORK_VERSION`
- `PHOTOS_MCP_DIST_DIR`
- `PHOTOS_MCP_BUILD_DIR`
- `PHOTOS_MCP_INSTALL_BUNDLE_PATH`
- `PHOTOS_MCP_FRAMEWORK_RUNTIME_DIR`
- `PHOTOS_MCP_SITE_PACKAGES_DIR`
- `PHOTOS_MCP_ICON_PYTHON`
- `PHOTOS_MCP_SKIP_PY2APP_CODESIGN`

## 5. import smoke contract

기준 파일:

- `scripts/smoke_bundle_imports.py`

이 스크립트는 `packaging_contract.py` 의 `PY2APP_PACKAGES + PY2APP_INCLUDES` 를 실제 import 해 보고, bundle import contract 가 깨졌는지 빠르게 확인한다.

동작 방식:

- source 환경이면 현재 interpreter 에서 import 확인
- `--bundle <PhotosMcp.app>` 를 주면 `Contents/Resources/lib`, `python*`, `lib-dynload` 를 `sys.path` 에 넣고 import 확인
- `sys.dont_write_bytecode = True` 로 `.pyc` 생성 억제

이 스크립트가 필요한 이유:

- source import 는 되는데 bundle 에서는 `__path__`, dynamic include, native module 누락으로 깨질 수 있다.
- py2app 결과를 전체 UI/MCP smoke 전에 더 좁게 검증할 수 있다.

## 6. test suite map

주요 테스트 파일과 의미는 아래와 같다.

- `tests/test_config.py`: config 기본값, env override, legacy fallback 규칙
- `tests/test_runtime_paths.py`: `~/.photos-mcp` 하위 경로 계산 규칙
- `tests/test_main.py`: `--health`, single-instance, health/capabilities endpoint, vendor namespace, bundle vendor root fallback
- `tests/test_runtime_bootstrap.py`: source/bundle `sys.path` bootstrap, terminal Python 선택
- `tests/test_state.py`: active/recent 분리, busy 전환, preflight aggregation, job payload normalization
- `tests/test_job_state.py`: DB/queue merge precedence, cancel persistence, terminal delete, clear history
- `tests/test_daemon.py`: controller 의 cancel/delete/clear adapter 위임
- `tests/test_preflight.py`: Photos read success/failure, automation warning downgrade, lightweight probe 우선 사용
- `tests/test_mcp_client.py`: mock MCP client 기준 tool listing, `health_status`, job payload normalization/state update
- `tests/test_packaging.py`: py2app kwargs, plist visible app contract, extras declaration, smoke script 존재, resource allowlist, staging cleanup, bundle name normalization
- `tests/test_vendor_import_inventory.py`: vendor-local top-level import 기준선 유지
- `tests/test_vendor_terminal_modes.py`: terminal helper mode, child env override, `PYTHONDONTWRITEBYTECODE=1`

## 7. 권장 validation 흐름

### 7.1 source-level

1. `uv run pytest -q`
2. 필요 시 focused test만 다시 실행
3. source import smoke 가 필요하면 `uv run python scripts/smoke_bundle_imports.py`

### 7.2 bundle-level

1. `./scripts/build_framework_standalone.sh`
2. `codesign --verify --deep --strict dist-framework-standalone/PhotosMcp.app`
3. `dist-framework-standalone/PhotosMcp.app/Contents/MacOS/PhotosMcp --health`
4. `python scripts/smoke_bundle_imports.py --bundle dist-framework-standalone/PhotosMcp.app`

### 7.3 runtime-level

1. app launch
2. `curl -fsS http://127.0.0.1:18791/health`
3. `curl -fsS http://127.0.0.1:18791/health/capabilities`
4. MCP `initialize`, `list_tools`, `call_tool(photos_status)`

### 7.4 reusable live validator

runtime checklist 를 반복 실행할 때는 아래 validator 를 우선 사용한다.

Apple source / workflow validation 기준:

- Apple candidate set 은 현재 still photo 만 대상으로 본다.
- `.mov`, `.mp4` 같은 video asset 은 `photos_library(action="list"|"ready_only")` 와 `photos_run(intent="curate")` 후보에서 먼저 제외한다.
- 따라서 live validator 의 Apple library/workflow 검증도 "영상이 안 섞이는지" 를 함께 본다.
- iCloud download 가 필요할 때도 helper / export fallback 은 photo asset 에만 적용하고, video asset 은 download 대상에서 건너뛴다.

기본 facade + wait-run validation:

1. `./.venv/bin/python scripts/live_validate.py --bundle-path "$HOME/Applications/PhotosMcp.app" --report-path docs/live-validation-report-latest.md`

wrapper launch 까지 포함한 validation:

1. `./.venv/bin/python scripts/live_validate.py --bundle-path "$HOME/Applications/PhotosMcp.app" --wrapper-script /Volumes/ExtData/02_Services/LocalModels/Nanobot/infra/scripts/run-photos-mcp-app.sh --report-path docs/live-validation-report-latest.md`

workflow validation 까지 포함한 validation:

1. `./.venv/bin/python scripts/live_validate.py --bundle-path "$HOME/Applications/PhotosMcp.app" --wrapper-script /Volumes/ExtData/02_Services/LocalModels/Nanobot/infra/scripts/run-photos-mcp-app.sh --include-workflows --report-path docs/live-validation-report-latest.md`

실행 중 진행 상태 확인:

- validator 는 markdown report 는 stdout 으로 유지하고, 현재 실행 중인 단계와 polling 상태는 stderr 로 timestamp prefix 와 함께 출력한다.
- 따라서 `--report-path` 를 써도 터미널에서는 `runtime / transport`, `photos_status`, `photos_library`, `photos_run` 진행 상황과 long-running polling 상태를 바로 볼 수 있다.
- progress 출력이 필요 없으면 `--quiet-progress` 를 추가한다.
- validator report의 evidence는 사진 ID, 파일명·경로, GPS, 앨범·인물 이름, thumbnail/base64, 장면 서술을 비식별 처리한다. 결과 상태·건수·오류 코드는 유지하므로 공유 가능한 운영 진단에 사용할 수 있다.

주의:

- `--include-workflows` 는 안전한 live 경로만 검증한다.
- Apple workflow 는 photo-only contract 기준으로 동작한다. target date range 안에 video 비중이 높아도 still photo 만 후보로 계산한다.
- `classify` 는 실제 local sample background run 으로 검증한다.
- `curate` 는 `writeback_mode=review` 로 검증한다.
- `organize` 는 Apple Photos write-back 대신 local output directory 로 검증한다.
- `import` 는 live Photos library mutation 을 피하기 위해 빈 input list no-op contract 로 검증한다.

### 7.5 LLM integration sample validator

LLM client 연결 시 사용할 목표 natural language 프롬프트와 그에 대응하는 facade tool route 를 실제 endpoint 에 대해 검증하려면 아래 validator 를 사용한다.

기준 스크립트:

- `./.venv/bin/python scripts/validate_llm_samples.py --report-path docs/llm-sample-validation-report-latest.md`

현재 validator 는 아래 3개 목표 프롬프트를 중심으로 돈다.

1. `iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들만 잘 나온 사진들만 앨범을 따로 저장해 만들어줘.`
2. `로컬 ~/SamplePhotos 디렉토리에 잘 나온 사진들을 골라서 iCloud 에 적절한 이름으로 앨범을 만들어 저장해줘.`
3. `iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들 중 특정인의 사진만 뽑아서 잘 나온 사진들을 로컬의 특정(~/temp) 디렉토리에 저장해줘.`

핵심 내부 정책:

- target date 기본값: 작년 `04-16` 부터 `04-30` 까지
- 잘 나온 사진 기준: top `30%`
- 화면 캡처 제외: `exclude_screenshots=true`
- 특정인 값: `--target-person` 또는 `PHOTOS_MCP_LLM_TARGET_PERSON`, 없으면 target date metadata 에서 auto-discovery 시도
- 실행 중 progress 는 stderr 와 날짜별 validator 로그 파일에 함께 기록된다.

필요하면 아래 override 를 준다.

1. `./.venv/bin/python scripts/validate_llm_samples.py --target-person '<actual person>' --samplephotos-dir ~/SamplePhotos --local-output-dir ~/temp --report-path docs/llm-sample-validation-report-latest.md`
2. `./.venv/bin/python scripts/validate_llm_samples.py --target-year 2025 --target-start-month 4 --target-start-day 16 --target-end-month 4 --target-end-day 30 --log-path ~/.photos-mcp/logs/manual/llm-sample-validation.log --report-path docs/llm-sample-validation-report-latest.md`

이 validator 는 LLM 자체를 호출하지 않는다. 대신 “이 자연어 요청이 planner 에 의해 적절한 facade tool route 로 번역되었다고 가정할 때, 실제 `photos-mcp` MCP endpoint 가 성공적으로 응답하는가”를 확인한다.

실행 전에 source 변경이 설치된 `PhotosMcp.app` 와 live endpoint 에 반영되어 있어야 한다. 즉 runtime 변경이 있으면 build/install 후 다시 앱을 띄운 상태에서 validator 를 돌려야 한다.

로그 관측 경로:

- 앱 런타임 로그: `~/.photos-mcp/logs/YYYY-MM-DD/photos-mcp-app.log`
- validator 진행 로그: `~/.photos-mcp/logs/YYYY-MM-DD/llm-sample-validation.log`
- launcher wrapper 로그: `~/.photos-mcp/logs/launcher.log`

validator 는 기본적으로 progress 를 stderr 에도 보여준다. 터미널에서 단계별 상태만 숨기고 싶으면 `--quiet-progress` 를 준다.

샘플 카탈로그와 해설은 `docs/18-llm-integration-sample-tests.md` 를 기준으로 본다.

## 8. 지금 문서화된 중요한 제약

- build 성공과 import smoke 성공은 다르다.
- `--vendor-runtime-smoke`는 번들에 포함된 `photo-source`, `FSEvents`, `osxphotos` import 체인을 실제로 확인한다. PyObjC 래퍼와 네이티브 확장 모듈이 서로 다른 번들 위치에 배치되어 생기는 오류를 build 단계에서 차단한다.
- import smoke 성공과 live Apple Photos permission success 도 다르다.
- source test 가 green 이어도 bundle-only import 문제가 남을 수 있다.
- bundle 이 살아 있어도 `photos_automation` 또는 `photos_thumbnail` warning 은 macOS permission/TCC, iCloud download, sample asset export 문제일 수 있다.

즉 검증은 source, bundle, runtime 세 층으로 나눠서 보는 편이 맞다.
