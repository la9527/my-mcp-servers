# 저장소 구조

```text
photos-mcp/
├── src/photos_mcp/
│   ├── app/                 composition root와 실행 설정
│   ├── domain/              모델, 정책, port
│   ├── application/         UI와 MCP가 공유하는 use case
│   ├── infrastructure/      저장소, source, VLM, vendor adapter
│   ├── interfaces/appkit/   macOS 화면과 view component
│   ├── interfaces/mcp/      public MCP와 health interface
│   ├── operations/          패키징과 실환경 검증
│   ├── vendor/              포함된 source·ranker 구현
│   └── *.py                 이전 import용 compatibility wrapper
├── scripts/                 빌드와 번들 smoke 도구
├── tests/                   단위·통합·AppKit·architecture 테스트
├── docs/                    현행 문서
├── pyproject.toml           패키지, 의존성, console entry point
└── setup.py                 py2app entry point
```

## 변경 위치 선택

| 변경 목적 | 우선 확인 위치 |
| --- | --- |
| MCP 공개 action과 transport | `interfaces/mcp/facade`, `interfaces/mcp/server.py` |
| 작업 실행·결과·선택 | `application/run_service.py`, `result_service.py`, `selection_service.py` |
| 쓰기 승인과 내보내기 | `application/mutation_*`, `export_service.py`, `write_service.py` |
| typed source와 정책 | `domain/models/source.py`, `domain/ports`, `domain/policies` |
| 상태·작업 기록 | `infrastructure/persistence` |
| 메인 AppKit UI | `interfaces/appkit/main` |
| 로컬 폴더 브라우저 | `interfaces/appkit/local_browser` |
| 결과 갤러리·뷰어 | `interfaces/appkit/results` |
| Apple·local·cloud source | `infrastructure/sources` |
| Linux/Mac VLM | `infrastructure/vision` |
| vendor 연결 | `infrastructure/vendor_adapter` |
| 앱 번들 | `operations/packaging`, `setup.py`, build script |

## 구현 규칙

1. 새 로직은 최상위 compatibility wrapper가 아니라 책임 package에 작성한다.
2. UI와 MCP가 같은 기능을 필요로 하면 `application` service에서 먼저 공유한다.
3. domain에는 AppKit, filesystem, network, SQLite와 vendor import를 추가하지 않는다.
4. source별 기능은 문자열 분기보다 descriptor, capability와 registry로 결정한다.
5. vendor에서 host 코드가 필요하면 `infrastructure/vendor_adapter/compat.py`만 사용한다.
6. PyObjC selector 이름과 기존 공개 class 이름은 설치 앱 smoke 전까지 변경하지 않는다.
7. 파일 이동 뒤 최상위 import 호환성과 py2app 수집을 반드시 검증한다.

## 호환 계층

`photos_mcp.main`, `photos_mcp.server`, `photos_mcp.photo_assets`, 기존 `*_appkit` 경로는 이전 호출자를 위해 새 구현을 re-export한다. `tests/architecture/test_public_import_compatibility.py`는 이전 경로와 새 경로가 같은 객체를 노출하는지 확인한다.

compatibility module은 제거 일정을 정하기 전까지 유지하며 새 기능이나 상태를 갖지 않는다.
