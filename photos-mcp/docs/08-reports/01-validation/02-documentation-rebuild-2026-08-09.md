# 문서 재구성 검증

## 범위

기존 설명을 현행 계약으로 재사용하지 않고 `src/photos_mcp`, `scripts`, `tests`, `pyproject.toml`에서 확인한 동작을 기준으로 문서 체계를 새로 구성했다.

## 환경

| 항목 | 값 |
| --- | --- |
| 날짜 | 2026-08-09 |
| 기준 revision | `c146402`와 작업 트리 변경 |
| 시스템 | Mac mini `Mac16,10` |
| 칩·메모리 | Apple M4, 32 GB |
| OS | macOS 26.5.2, build 25F84 |
| architecture | arm64 |
| Python | 3.12.13 |

## 검증 결과

| 검증 | 명령 | 결과 |
| --- | --- | --- |
| 문서 링크·번호 구조 | `./.venv/bin/python scripts/validate_docs.py` | 통과 |
| 문서·action 계약 | `./.venv/bin/pytest tests/test_documentation_structure.py tests/test_action_contract_docs.py -q` | 3 passed |
| 전체 회귀 | `./.venv/bin/pytest -q` | 427 passed, 4.07초 |
| whitespace | `git diff --check` | 통과 |

문서 검증 시 루트 README, 현행 문서 36개, archive 안내 1개를 합한 Markdown 38개가 인덱스에서 도달 가능했다. 깨진 로컬 링크, 현행 문서의 archive 의존, `README.md` 예외를 제외한 숫자 접두사 규칙 위반은 없었다.

## 확인된 경계

- 이번 검증은 문서 구조와 자동 테스트 회귀를 확인했다.
- standalone 앱 재빌드와 codesign smoke는 실행하지 않았다.
- 실제 Apple 사진 권한, iCloud 다운로드, Linux VLM 호출은 이번 문서 작업에서 재실행하지 않았다.
- 과거 문서는 `docs/99-archive/99-legacy-2026-08-09`에 보존되지만 현행 계약의 근거가 아니다.
