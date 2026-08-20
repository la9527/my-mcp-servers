# 2026-08-20 Google Photos 취소·만료 예외 흐름 검증

## 범위

개인 단독 사용자가 Mac과 동일 LAN의 Linux Qwen3.8 runtime을 직접 관리하는 설치를 대상으로 Google Photos Picker의 취소·재선택·만료 정리와 복구 계약을 확인했다. Google OAuth와 Picker에서의 사용자 직접 선택은 유지하며, 이 개인 설치에서는 별도의 Linux 전송 확인 sheet를 표시하지 않는다.

## 결과

| 항목 | 방법 | 결과 |
| --- | --- | --- |
| 선택 취소 | 실제 AppKit 화면에서 Picker 대기 중 `선택 취소` 실행 | 통과. 즉시 `사진 선택을 취소했습니다`와 `새 선택 시작`으로 복귀 |
| 취소 후 재선택 | 실제 앱에서 새 Picker session 생성 뒤 다시 취소 | 통과. 두 번째 session도 정상 생성·정리 |
| 만료 session 정리 | 실제 Keychain 연결로 빈 Picker session을 생성하고, 테스트용 로컬 만료 시각을 적용한 뒤 poll | 통과. `timed_out`, `picker_session_timed_out`으로 전환하고 원격 session 삭제 |
| refresh token 사용 | 위 실제 Picker session 생성 요청 | 통과. Keychain credential로 access token을 자동 갱신해 create/delete 호출 성공 |
| `invalid_grant` 재연결 경계 | REST adapter 계약 테스트 | 통과. 만료·철회 token은 `GooglePhotosReauthorizationRequired`로 변환 |
| 업로드 부분 실패·재개 | upload·resume·mutation contract 테스트 | 통과. receipt 기반 partial 상태와 재시도 경로 유지 |
| 새 선택 전 임시 cache 정리 | 실제 앱에서 준비된 Google 선택을 `새 사진 선택` 후 취소하고 SQLite lease 확인 | 통과. 미연결 lease 0개, 완료 작업에 연결된 lease 56개만 유지 |

## 수정

Picker 대기 중 polling thread가 `_worker`를 점유해 `선택 취소` 작업을 시작하지 못하는 결함을 수정했다. 취소 요청은 polling을 중지하고 background thread에서 polling 종료를 최대 5초 기다린 뒤 Google session을 삭제한다. 새 선택을 시작할 때는 기존의 준비된 Picker cache를 runtime worker에서 먼저 해제하고, 메인 분류 화면도 `사진을 선택해 주세요` 상태로 즉시 초기화한다.

또한 이미 분석 작업에 연결된 lease는 상태 저장소의 일시적 부재와 무관하게 재선택 후보로 복구하지 않도록 했다. 취소·재선택 정리는 이미지 파일과 Google Picker metadata JSON sidecar를 함께 삭제한다.

## 실행 검증

```text
./.venv/bin/pytest -q \
  tests/test_google_photos_appkit.py \
  tests/test_cloud_source_adapters.py \
  tests/test_google_photos_rest_adapters.py

21 passed

./.venv/bin/pytest -q \
  tests/test_cloud_source_adapters.py::test_expired_picker_session_times_out_and_cleans_provider \
  tests/test_google_photos_rest_adapters.py::test_invalid_grant_requires_reauthorization \
  tests/test_google_photos_upload_service.py \
  tests/test_selected_export_resume.py \
  tests/test_mutation_safety.py

16 passed

./.venv/bin/pytest -q

622 passed
```

## 남은 실제 계정 검증

- Google 계정 설정에서 refresh token을 철회한 뒤 앱의 재연결 UX를 확인한다. 이 작업은 기존 장기 연결을 끊으므로 사용자가 원할 때만 수행한다.
- 실제 대용량 업로드 도중 네트워크 단절을 유발해 partial receipt 재개와 Google album 중복 방지를 확인한다. 기존 원본이나 album을 변경하지 않는 별도 테스트 사본으로만 수행한다.
- 자연 Picker 만료는 최대 60분 대기가 필요하다. 현재는 실제 session delete와 만료 state 전환을 안전하게 검증했으며, 장시간 대기 검증은 위 두 항목을 진행할 때 함께 수행한다.
