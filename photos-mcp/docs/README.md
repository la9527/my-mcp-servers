# Photos MCP 문서

> 상태: 현행 문서 인덱스
>
> 검증 기준: `src/photos_mcp`, `scripts`, `tests`, `pyproject.toml`
> 최종 구조 검토: 2026-08-09

이 문서는 Photos MCP의 공식 문서 진입점이다. 현재 기능은 실제 코드와 자동 테스트를 기준으로 설명하며, 과거 문서는 [archive](99-archive/README.md)에만 보존한다.

## 독자별 시작점

| 목적 | 먼저 읽을 문서 |
| --- | --- |
| 앱을 처음 설치하고 실행 | [설치와 실행](01-getting-started/02-installation.md) |
| 사진을 실제로 분류 | [첫 실행](01-getting-started/03-first-run.md) |
| Apple 사진 사용 | [Apple 사진 분류](02-user-guide/02-apple-photos-classification.md) |
| 로컬 폴더와 RAW 파일 사용 | [로컬 폴더 분류](02-user-guide/03-local-folder-classification.md) |
| Nanobot 또는 다른 MCP client 연결 | [MCP 개요](03-integration/README.md) |
| 전체 내부 구조 이해 | [시스템 개요](04-architecture/README.md) |
| 빌드와 설치본 검증 | [빌드와 배포](05-operations/02-build-and-release.md) |
| 장애 원인 확인 | [문제 해결](05-operations/04-troubleshooting.md) |
| AppKit 화면 수정 | [디자인 시스템](07-design-system/README.md) |

## 문서 구조

- `01-getting-started`: 제품 개요, 설치, 첫 실행
- `02-user-guide`: 앱 화면과 사용자 작업 흐름
- `03-integration`: MCP, Nanobot, VLM 연결 계약
- `04-architecture`: 런타임, 데이터 흐름, 저장소, 안전 경계
- `05-operations`: 설정, 빌드, 상태 확인, 장애 대응
- `06-development`: 저장소 구조, 테스트, 문서 관리 원칙
- `07-design-system`: AppKit 디자인·컴포넌트·접근성 기준
- `08-reports`: 재현 가능한 검증 및 벤치마크 결과
- `09-roadmap`: 아직 구현되지 않은 활성 계획만 관리
- `99-archive`: 이전 문서 보존 영역. 현행 계약으로 인용하지 않는다.

## 전체 문서 목록

### 시작

- [제품 개요](01-getting-started/README.md)
- [설치와 실행](01-getting-started/02-installation.md)
- [첫 실행](01-getting-started/03-first-run.md)

### 사용자 가이드

- [앱 화면 안내](02-user-guide/README.md)
- [Apple 사진 분류](02-user-guide/02-apple-photos-classification.md)
- [로컬 폴더 분류](02-user-guide/03-local-folder-classification.md)
- [결과 검토와 내보내기](02-user-guide/04-results-and-export.md)
- [키보드 조작](02-user-guide/05-keyboard-shortcuts.md)

### 통합

- [MCP 통합 개요](03-integration/README.md)
- [MCP 도구 참조](03-integration/02-tool-reference.md)
- [Nanobot 연결](03-integration/03-nanobot-integration.md)
- [이미지 분석 런타임](03-integration/04-vision-runtime.md)

### 아키텍처

- [시스템 아키텍처](04-architecture/README.md)
- [런타임 생명주기](04-architecture/02-runtime-lifecycle.md)
- [요청과 작업 흐름](04-architecture/03-request-and-job-flow.md)
- [저장소와 데이터 모델](04-architecture/04-storage-model.md)
- [보안과 개인정보](04-architecture/05-security-and-privacy.md)

### 운영과 개발

- [설정](05-operations/README.md)
- [빌드와 배포](05-operations/02-build-and-release.md)
- [상태와 모니터링](05-operations/03-health-and-monitoring.md)
- [문제 해결](05-operations/04-troubleshooting.md)
- [저장소 구조](06-development/README.md)
- [테스트](06-development/02-testing.md)
- [문서 관리 원칙](06-development/03-documentation-policy.md)

### UI, 보고서, 계획

- [AppKit 디자인 시스템](07-design-system/README.md)
- [검증 보고서](08-reports/README.md)
- [로드맵](09-roadmap/README.md)
- [이전 문서 보관소](99-archive/README.md)

## 제품 경계

Photos MCP는 다음 두 표면을 제공한다.

1. 사용자가 직접 조작하는 macOS AppKit 앱
2. `http://127.0.0.1:18791/mcp`에 노출되는 Streamable HTTP MCP server

MCP public tool은 다음 네 개다.

- `photos_query`: 조회, 상태, 가이드, 결과 확인
- `photos_select`: 분석과 사진 선택
- `photos_write`: 앨범 및 파일 내보내기
- `photos_workflow`: 여러 단계를 묶은 장기 작업

쓰기 작업은 분석과 분리되며 승인 계획을 거친다. 자세한 계약은 [MCP 도구 참조](03-integration/02-tool-reference.md)를 따른다.

## 문서 신뢰 규칙

- 동작 설명은 코드 경로와 테스트 근거를 함께 적는다.
- 구현되지 않은 내용은 현행 문서에 섞지 않고 `roadmap/active`에 둔다.
- 실행 결과는 `reports`에 두며 일반 사용법과 섞지 않는다.
- `archive` 문서는 역사 기록일 뿐 현재 사용법이 아니다.
- MCP action 목록은 `src/photos_mcp/facade/action_options.py`와 일치해야 한다.

## 빠른 상태 확인

```bash
curl -fsS http://127.0.0.1:18791/health
curl -fsS http://127.0.0.1:18791/health/capabilities
```

정상 판단은 단순히 프로세스가 존재하는지가 아니라 `daemon_status`가 `ready` 또는 작업 중인 `busy`인지, 필요한 capability가 준비됐는지를 함께 확인한다.
