# PhotosMcp 활성 로드맵 정리 검증

- 점검일: 2026-09-01 (Asia/Seoul)
- 기준 revision: `a380df4` 및 이 문서를 포함한 후속 문서 정리 commit
- 환경: macOS 26.5.2 (25F84) 로컬 host, PhotosMcp 저장소
- 범위: 문서 분류·링크·회귀 검증. 앱 설정, 외부 계정, 사진 원본과 운영 데이터는 변경하지 않음.

## 1. 결론

활성 로드맵 12건 중 구현과 회귀 검증이 끝난 7건을 `docs/99-archive/01-completed-roadmap`으로 이동했다. 활성 목록에는 장기 표본 수집, Google 외부 계정 E2E 또는 사용자 결정이 실제로 남은 5개 계획만 유지했다.

이동한 계획:

1. Google Photos 재선택과 작업 기록 정리
2. 소스 인식형 사진 분류와 Google Photos 자동 준비
3. 장면 대표 한 장 결과 갤러리
4. 작업 기록 신뢰성 있는 삭제와 진행 상태
5. Google Photos 원본 및 메타데이터 보존
6. 개인 얼굴 관리 UI
7. 개인 얼굴 묶음 드래그 앤 드롭 UX

관련 문서에서 쓰는 이미지 5개도 완료 보관소의 `01-assets`로 함께 이동해 상대 링크를 보존했다.

## 2. 완료 근거

| 영역 | 구현·검증 근거 | 판정 |
|---|---|---|
| Google 재선택 | 제출·취소 뒤 새 세션과 준비 요약 초기화 controller 테스트 | 완료 |
| source-aware workflow | Apple·로컬·Google 분기, 자동 준비, 실제 설치 앱 확인, 당시 전체 595건 | 완료 |
| 결과 갤러리 | 장면 대표·대안 비교, 실제 Google 50장, 당시 전체 604건 | 완료 |
| 기록 삭제 | workflow/vendor 통합 삭제, 복구 대기 상태, cache 안전 경계와 진행률 테스트 | 완료 |
| 원본·메타데이터 | `=d`/`=dv`, sidecar, Takeout 출처 구분과 삭제 테스트 | 완료 |
| 얼굴 관리 UI | private registry `0600`, 이름·병합·분리·삭제와 재로딩 테스트 | 완료 |
| 얼굴 drag-and-drop | opaque ID drop, undo, layout·실제 앱 검증, 당시 전체 651건 | 완료 |

과거 계획에 기록된 전체 테스트 수는 각 기능 완료 당시의 증거다. 이번 정리 시점에는 현재 HEAD 전체 suite를 다시 실행해 최신 기준선도 별도로 확인한다.

## 3. 활성 상태로 남긴 항목

- 추천 품질 검토: 새 500~1,000장 작업에서 `duplicate` 양성 label 20개를 자연 수집해야 한다.
- 인물 구성 shadow 순위: 독립 holdout 사람 검토는 끝났지만 통계·정확도 gate가 부족해 운영 반영하지 않는다.
- Google Photos Picker 실연동: refresh token 철회 뒤 재연결, 실제 네트워크 단절 기반 부분 업로드 복구, 앨범 중복 방지와 자연 만료 검증이 남아 있다.
- Google Photos AppKit UX: 외부 설정이 완료된 실제 계정 화면 검증이 남아 있다.
- Google Photos OAuth 앱 설정: callback URL 자동 수신과 실제 계정 E2E가 남아 있다.

외부 계정 상태 변경, 네트워크 장애 주입과 인위적인 label 생성은 이번 저위험 정리에 포함하지 않았다.

## 4. 검증 절차

```bash
.venv/bin/python -m pytest -q
git diff --check
```

추가로 활성·완료 README의 로컬 Markdown 링크와 이동 문서의 이미지 상대 경로가 실제 파일로 해석되는지 검사한다. 실행 결과와 최종 commit은 이 문서가 포함된 변경 이력에 남긴다.

## 5. 실행 결과

- 현행 문서 구조·로컬 링크: 69개 검증 통과
- 완료 보관소로 이동한 이미지 링크: 5/5 파일 존재 확인
- 전체 자동 회귀: 654 passed, 7.10초
- `git diff --check`: 통과
- 앱 설정, 외부 계정, 사진 원본, private review와 운영 runtime 데이터 변경: 없음
