# 실행 검증 보고서

실제 앱 또는 MCP 검증 결과를 날짜가 포함된 Markdown 파일로 추가한다.

권장 파일명:

```text
YYYY-MM-DD-<검증-대상>.md
```

자동 테스트 결과는 전체 통과 수, 실행 시간, 명령을 함께 기록한다. Apple 사진 검증은 사진 원본이나 인물 이름을 문서에 포함하지 않고 개수와 상태만 남긴다.

## 보고서

- [2026-08-09 문서 재구성 검증](02-documentation-rebuild-2026-08-09.md)
- [2026-08-09 standalone 앱 빌드 및 화면 검증](03-standalone-app-build-and-ui-validation-2026-08-09.md)
- [2026-08-09 로컬 사진 500장 E2E 검증](02-local-500-photo-e2e-2026-08-09.md)
- [2026-08-09 결과 갤러리·상대 추천 기준 검증](04-result-gallery-relative-recommendation-2026-08-09.md)
- [2026-08-09 RAW 전체 화면 뷰어 캐시 검증](05-raw-viewer-preview-cache-2026-08-09.md)
- [2026-08-09 코드베이스 리팩터링 검증](06-codebase-refactoring-2026-08-09.md)
- [2026-08-10 리팩터링 실환경 회귀 검증](07-refactor-real-environment-regression-2026-08-10.md)
- [2026-08-10 추천 품질 사람 검토 UI 검증](08-recommendation-quality-review-ui-2026-08-10.md)
- [2026-08-10 추천 품질 사람 기준선 및 shadow 점수 검증](09-recommendation-shadow-score-2026-08-10.md)
- [2026-08-10 두 번째 추천 시각적 다양성 shadow 검증](10-recommendation-second-diversity-shadow-2026-08-10.md)
- [2026-08-10 인물 구성 장면 분리와 얼굴 품질 shadow 검증](11-person-aware-scene-shadow-2026-08-10.md)
- [2026-08-11 동일 인물 사진 pairwise VLM shadow 검증](12-person-pairwise-shadow-2026-08-11.md)
- [2026-08-11 인물 구성 라벨링·SFace 보정 기반 검증](13-person-composition-calibration-foundation-2026-08-11.md)
- [2026-08-14 얼굴 crop pair 직접 보정 검증](14-face-identity-pair-calibration-2026-08-14.md)
- [2026-08-14 얼굴 동일인 이중 임계값 shadow 검증](15-face-identity-dual-threshold-shadow-2026-08-14.md)
- [2026-08-14 얼굴 동일인 constrained grouping shadow 검증](16-face-identity-constrained-grouping-shadow-2026-08-14.md)
- [2026-08-14 복수 지지 병합 private audit UI 검증](17-face-identity-multi-support-audit-ui-2026-08-14.md)
- [2026-08-14 복수 지지 병합 audit 결과와 독립 holdout](18-face-identity-multi-support-audit-result-2026-08-14.md)
- [2026-08-14 동일 주 피사체 얼굴·표정 순위 shadow 검증](19-person-face-expression-pairwise-shadow-2026-08-14.md)
- [2026-08-14 활성 로드맵 자동 구현·회귀 검증](20-automated-roadmap-implementation-2026-08-14.md)
- [2026-08-20 Google Photos 실계정 E2E 검증](21-google-photos-real-account-e2e-2026-08-20.md)
- [2026-08-20 Linux Qwen3.8 VLM 기본값·실사진 검증](22-linux-qwen38-vision-runtime-2026-08-20.md)
- [2026-08-20 Google Photos 취소·만료 예외 흐름 검증](23-google-photos-exception-flow-2026-08-20.md)
- [2026-08-21 추천 다양성 검토 큐 재준비](24-recommendation-diversity-review-refresh-2026-08-21.md): 삭제된 과거 작업과 분리해 현재 보존된 결과의 개인 검토 큐를 다시 만들고, 두 번째 추천 중복 label 재수집 기준을 기록했다.
- [2026-08-22 추천 다양성 사람 검토 결과](25-recommendation-diversity-review-result-2026-08-22.md): 23개 복수 사진 장면의 개인 검토 집계와 shadow 재현성 보정, 현행 정책 유지 결론을 기록했다.
- [2026-09-01 독립 얼굴 holdout 완료와 readiness 재집계](26-independent-face-holdout-completion-2026-09-01.md): 5쌍 사람 검토 완료와 통계 부족 분리, aggregate-only 재집계 결과를 기록했다.
- [2026-09-01 활성 로드맵 정리 검증](27-active-roadmap-cleanup-2026-09-01.md): 구현·회귀 검증이 끝난 계획 7건과 관련 시안을 완료 보관소로 이동하고 실제 미완료 후보만 활성 목록에 남긴 근거를 기록했다.
