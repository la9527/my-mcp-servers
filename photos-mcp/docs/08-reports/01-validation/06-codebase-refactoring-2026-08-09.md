# 코드베이스 리팩터링 검증

## 범위

`03-codebase-refactoring-2026-08-09.md`의 0~7단계를 기존 사용자 기능과 MCP 계약을 유지하는 조건으로 적용했다. 검증은 source import만 확인하지 않고 전체 pytest, 문서, py2app standalone bundle과 번들 내부 runtime을 포함했다.

## 구조 결과

| 항목 | 결과 |
| --- | --- |
| 책임 package | `app`, `domain`, `application`, `infrastructure`, `interfaces`, `operations` 구성 |
| 공개 호환성 | 기존 CLI, MCP, AppKit, domain·vendor loader import가 새 구현을 재노출 |
| domain 경계 | framework, host adapter, vendor import 없음 |
| application 경계 | AppKit, MCP interface와 vendor package 직접 import 없음 |
| vendor 경계 | host import는 `infrastructure/vendor_adapter/compat.py`로 제한 |
| AppKit component | 로컬 folder tree·grid·single view·layout, 결과 collection item 분리 |
| cloud source | GCS adapter와 Google Picker fake lifecycle·Keychain·정책 gate 분리 |

## 자동 검증

| 검사 | 결과 |
| --- | --- |
| 전체 pytest | `466 passed in 4.66s` |
| 기준선 대비 | 446건에서 20건 증가, 삭제된 회귀 테스트 없음 |
| 공개 import 호환성 | legacy와 새 경로의 동일 객체 연결 통과 |
| architecture 규칙 | domain, application, vendor import 규칙 통과 |
| Google Picker fake | create, poll, pagination, consume, timeout, cancel, cleanup 통과 |
| source policy | Google 얼굴 품질·군집 차단, GCS·Google 분리 통과 |

## standalone 검증

기존 설치 앱을 덮어쓰지 않도록 다음 별도 경로에 빌드했다.

- 빌드본: `dist-framework-standalone/PhotosMcp.app`
- 검증 설치본: `build-refactor-install/PhotosMcp.app`
- 검증 링크: `build-refactor-install/PhotosMcp-public.app`

| 검사 | 결과 |
| --- | --- |
| py2app framework standalone build | 통과 |
| `codesign --verify --deep --strict` | 통과 |
| `--health` | `status=ok` |
| `--runtime-import-smoke` | `runtime=osxphotos`, 통과 |
| `--vendor-runtime-smoke` | `photo-source`, `photo-ranker-vision`, 통과 |
| 번들 import inventory | 통과 |

현재 번들은 약 545MB다. 대부분은 pyarrow, scipy, lib-dynload, pandas와 numpy 같은 포함 runtime이며 리팩터링 package의 중복 포함은 발견되지 않았다. 설치 위치에 따라 health payload의 `bundle_path`가 기본 설정 경로를 표시하는 기존 진단 특성은 기능 실패가 아니다.

## 기능 동일성 판정

이번 변경은 DB schema, 공개 MCP action, mutation approval, 결과 envelope, 사진 읽기·쓰기 정책, AppKit selector와 keyboard shortcut을 변경하지 않았다. 기존 500장 ARW/JPEG E2E와 승인 기반 원본·XMP 내보내기 결과를 회귀 기준으로 유지했고, 변경 후 관련 AppKit·RAW·분류·내보내기·상태 복구 테스트와 번들 runtime smoke가 통과했다.

실제 사진 앨범에 대한 쓰기 E2E와 Google 계정 OAuth는 자동으로 실행하지 않았다. 전자는 사용자 데이터를 변경할 수 있고 후자는 아직 실제 계정 연결 단계가 아니므로, 기존 승인 기반 검증 절차와 후속 cloud 연동 작업으로 유지한다.

## 결론

리팩터링된 코드는 기존 기능과 진입 경로를 유지하면서 새 계층 구조로 빌드·실행할 수 있다. 자동 회귀, 문서, standalone 서명과 runtime import 기준으로 현재 시스템에서 사용할 수 있는 상태다.
