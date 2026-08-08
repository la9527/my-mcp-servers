# 문서 관리 원칙

## 기준

- 현행 문서는 현재 코드와 자동 테스트만 근거로 작성한다.
- 과거 문서의 설명을 복사해 새 계약으로 사용하지 않는다.
- 구현 전 아이디어는 `roadmap/active`에 두고 사용자 가이드에 섞지 않는다.
- 일회성 측정값은 `reports`에 두고 기능 보장처럼 표현하지 않는다.
- 문서는 한국어를 기본으로 하며 코드 식별자와 명령은 원문을 유지한다.

## 변경 규칙

| 코드 변경 | 함께 갱신할 문서 |
| --- | --- |
| 공개 action 추가·삭제 | `integration/tool-reference.md`, 계약 테스트 |
| 환경 변수·기본 port 변경 | `operations/configuration.md` |
| 데이터 저장 경로·schema 변경 | `architecture/storage-model.md` |
| UI 탐색·키보드 변경 | `user-guide`, `design-system` |
| 빌드 smoke 변경 | `operations/build-and-release.md` |
| 구현되지 않은 새 기능 | `roadmap/active` |

## 링크 규칙

- 저장소 안의 문서는 상대 링크를 사용한다.
- 현행 문서에서 `archive`를 기능 근거로 링크하지 않는다.
- 이미지에는 의미 있는 대체 텍스트를 둔다.
- 경로 변경 후 `scripts/validate_docs.py`를 실행한다.

## 파일명과 디렉토리 순서

- `README.md`를 제외한 `docs` 아래 현행 디렉토리와 Markdown 파일은 `01-`, `02-`처럼 두 자리 숫자 접두사로 시작한다.
- 숫자는 독자가 읽을 중요 순서와 작업 흐름을 나타낸다.
- 각 영역의 진입 문서는 `README.md`, 전체 진입 문서는 `docs/README.md`로 둔다.
- 새 문서를 중간에 넣어야 하면 관련 링크와 검증 테스트를 함께 갱신한다.
- `docs/99-archive/99-legacy-*` 안의 원본 파일명은 역사 보존을 위해 번호 규칙 검사에서 제외한다.
- 저장소 루트와 각 문서 디렉토리의 `README.md`는 진입점이므로 번호 접두사 예외다.

## 완료 정의

문서 작업은 파일을 작성한 것만으로 끝나지 않는다. 링크 검사, action 계약 검사, 관련 테스트, `git diff --check`가 통과하고 문서 인덱스에서 도달 가능해야 완료다.
