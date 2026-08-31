# standalone 앱 빌드 및 화면 검증

## 목적

문서 재구성 이후의 소스로 `PhotosMcp.app`을 새로 만들고, 실제 설치본의 서명·내장 런타임·HTTP 서비스·주요 화면을 독립적으로 검증했다. 기존에 실행 중이던 앱은 먼저 종료해 포트와 단일 실행 잠금을 해제한 뒤 새 설치본만 실행했다.

## 환경

| 항목 | 값 |
| --- | --- |
| 날짜 | 2026-08-09 |
| 기준 revision | `30bfffd` |
| 시스템 | Mac mini `Mac16,10` |
| 칩·메모리 | Apple M4, 32 GB |
| OS | macOS 26.5.2, build 25F84 |
| architecture | arm64 |
| Python | Homebrew Python 3.12.13 |
| bundle ID | `com.nanobot.photos-mcp` |
| 앱 버전 | `0.1.0` |

## 빌드와 설치

다음 명령으로 framework standalone bundle을 다시 만들었다.

```bash
PHOTOS_MCP_INSTALL_BUNDLE_PATH=/Volumes/ExtData/02_Services/PhotosMcp/PhotosMcp.app \
PHOTOS_MCP_PUBLIC_APPLICATION_LINK=/Applications/PhotosMcp.app \
./scripts/build_framework_standalone.sh
```

설치 결과는 다음과 같다.

| 항목 | 결과 |
| --- | --- |
| 빌드 bundle | `dist-framework-standalone/PhotosMcp.app`, 약 320 MB |
| 실제 설치본 | `/Volumes/ExtData/02_Services/PhotosMcp/PhotosMcp.app`, 약 320 MB |
| 사용자 진입 경로 | `/Applications/PhotosMcp.app` 심볼릭 링크 |
| 심볼릭 링크 대상 | 실제 설치본과 일치 |
| 설치 시각 | 2026-08-09 08:06 KST |

## 자동 smoke 결과

빌드 스크립트가 빌드 bundle과 설치 bundle 각각에 같은 검사를 실행했으며 모두 통과했다.

| 검사 | 결과 |
| --- | --- |
| `--health` | `status=ok` |
| `--runtime-import-smoke` | `runtime=osxphotos`, 통과 |
| `--vendor-runtime-smoke` | `runtime=photo-source`, `scene_runtime=photo-ranker-vision`, 통과 |
| `codesign --verify --deep --strict` | 빌드본·설치본 모두 통과 |
| designated requirement | `identifier "com.nanobot.photos-mcp"` 확인 |

빌드 중 setuptools 설치 방식 deprecation 경고와 서드파티 모듈의 `SyntaxWarning`이 출력됐지만, bundle 생성·서명·세 smoke 검사에는 영향을 주지 않았다.

## 실제 실행 결과

`open /Applications/PhotosMcp.app`으로 설치본을 실행했다. 실행 파일 PID가 새로 생성됐고 `127.0.0.1:18791`에서 수신 중임을 확인했다.

| 검사 | 결과 |
| --- | --- |
| 프로세스 경로 | 실제 설치본의 `Contents/MacOS/PhotosMcp` |
| MCP/HTTP 포트 | `127.0.0.1:18791`, LISTEN |
| `/health` | `status=ok`, `daemon_status=ready` |
| Apple 사진 접근 권한 | 통과 |
| Apple 사진 보관함 읽기 | 통과, 읽기 전용 DB 열림 |
| 활성 작업 | 0건 |
| 최근 작업 | 52건 조회 |
| 시작 이후 로그 | 예외·traceback 없음 |

전체 preflight 상태가 `warning`인 것은 시작 시간을 지연시키지 않도록 사진 미리보기와 앨범 변경 권한 검사를 명시적 요청 전까지 보류하기 때문이다. 기본 기능 실패가 아니다. Linux VLM도 `on_demand` 정책이므로 이번 UI smoke 중에는 워크스테이션을 깨우거나 모델을 올리지 않았고 `ready=false`가 정상이다.

## 화면 검증

Computer Use로 실제 설치본을 조작해 다음 경로를 확인했다. 개인 사진 캡처, 파일명, 인물 이름, 작업 ID는 저장소에 기록하지 않았다.

| 화면 | 확인 결과 |
| --- | --- |
| 홈 | 서버 상태, 사진 분류 진입, 최근 작업 3건, 환경 검사 진입이 겹침 없이 노출됨 |
| 사진 분류 | Apple 사진·로컬 폴더 진입, 범위와 작업 설정, 예상 수량, 시작·취소 버튼이 정상 노출됨 |
| 작업 기록 | 상태 필터, 스크롤 목록, 선택 작업 상세, 결과 보기 버튼이 정상 동작함 |
| 결과 보기 | 100건 결과, 추천·검토 필요 필터, 자동 6열 격자, 선택 체크, 우측 inspector가 정상 노출됨 |
| 결과 전체 화면 | 창 확장 시 격자와 우측 inspector가 화면 너비에 맞춰 확장되고 겹치지 않음 |
| 환경 및 권한 | 기본 3개 상태와 선택 검사 2개, Linux VLM 요청 시 연결 상태가 구분되어 노출됨 |

결과 창에서는 `전체`, `추천`, `검토 필요` 합계가 일치했고, 선택된 사진 수와 내보내기 버튼 문구도 함께 갱신됐다. 이번 검증은 기존 완료 결과를 읽기 전용으로 열었으며 새 분석이나 사진·앨범 변경은 실행하지 않았다.

## 확인된 후속 항목

- `/health`의 `bundle_path` 로그는 실제 실행 경로가 아니라 기본 설정인 `~/Applications/PhotosMcp.app`을 표시한다. 실행 프로세스와 공개 링크는 올바른 설치본을 사용하지만 진단 정보의 경로 표시는 실제 bundle 기준으로 정리할 필요가 있다.
- `~/Applications/PhotosMcp.app`에 과거 설치본이 남아 있다. 현재 공개 진입 경로에는 영향이 없지만 장기적으로 canonical 설치 위치를 하나로 고정하는 편이 안전하다.
- 사진 미리보기, 앨범 변경 권한, iCloud 원본 다운로드, RAW/HEIC 입력, Linux VLM 호출, Apple 사진 앨범 및 로컬 디렉토리 내보내기는 다음 E2E 검증에서 명시적으로 실행해야 한다.

## 결론

새 standalone 앱은 현재 Mac mini에서 빌드·서명·설치·기본 런타임·화면 진입 기준으로 사용할 수 있다. 이번 범위는 통과이며, 사진과 외부 시스템을 실제로 사용하는 변경성 흐름은 별도의 E2E 검증 대상으로 남긴다.
