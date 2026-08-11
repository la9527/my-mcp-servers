# 테스트

## 전체 테스트

```bash
./.venv/bin/pytest -q
```

현재 pytest는 `pyproject.toml`의 `pythonpath = ["src"]`, `testpaths = ["tests"]`를 사용한다.

## 변경 영역별 빠른 검증

```bash
# MCP 계약과 facade
./.venv/bin/pytest tests/test_action_contract_docs.py tests/test_public_tools.py tests/test_facade_common.py -q

# AppKit 화면과 레이아웃
./.venv/bin/pytest tests/test_menu_appkit_layout.py tests/test_result_gallery_appkit.py tests/test_recommendation_review_appkit.py tests/test_photo_viewer_appkit.py -q

# 인물 구성 보정·승격 안전 게이트
./.venv/bin/pytest tests/test_person_scene_shadow.py tests/test_person_pairwise_shadow.py tests/test_person_composition_calibration.py tests/test_person_promotion.py -q

# 로컬 파일과 내보내기
./.venv/bin/pytest tests/test_photo_assets.py tests/test_local_writer.py tests/test_selected_export_bundle.py -q

# 런타임과 패키징
./.venv/bin/pytest tests/test_runtime_bootstrap.py tests/test_packaging.py tests/test_main.py -q

# 계층 의존성과 이전 import 호환성
./.venv/bin/pytest tests/architecture -q
```

## 문서 검증

```bash
./.venv/bin/python scripts/validate_docs.py
./.venv/bin/pytest tests/test_documentation_structure.py tests/test_action_contract_docs.py -q
```

문서 검증은 내부 Markdown 링크, 필수 문서, archive 격리, 공개 action 계약을 확인한다.

## 실제 환경 검증

단위 테스트는 macOS 권한, 실제 Apple 사진 보관함, Linux VLM 준비를 완전히 대신하지 못한다. 설치본에서는 다음 순서로 별도 확인한다.

1. bundle smoke 세 개를 실행한다.
2. 앱의 환경 검사를 실행한다.
3. 읽기 전용 사진 한 장 분석을 수행한다.
4. 소규모 로컬 폴더 분류를 수행한다.
5. 테스트 목적지에 승인 기반 내보내기를 수행한다.

실제 검증 결과는 날짜와 환경을 포함해 `docs/08-reports/01-validation`에 기록한다.

## standalone 번들 검증

설치 위치를 덮어쓰지 않는 리팩터링 검증 빌드는 다음처럼 별도 경로를 사용한다.

```bash
PHOTOS_MCP_INSTALL_BUNDLE_PATH="$PWD/build-refactor-install/PhotosMcp.app" \
PHOTOS_MCP_PUBLIC_APPLICATION_LINK="$PWD/build-refactor-install/PhotosMcp-public.app" \
./scripts/build_framework_standalone.sh
```

빌드 뒤에는 source 실행이 아니라 번들 실행 파일을 직접 검사한다.

```bash
BUNDLE="$PWD/dist-framework-standalone/PhotosMcp.app"
codesign --verify --deep --strict "$BUNDLE"
"$BUNDLE/Contents/MacOS/PhotosMcp" --health
"$BUNDLE/Contents/MacOS/PhotosMcp" --runtime-import-smoke
"$BUNDLE/Contents/MacOS/PhotosMcp" --vendor-runtime-smoke
./.venv/bin/python scripts/smoke_bundle_imports.py --bundle "$BUNDLE"
```
