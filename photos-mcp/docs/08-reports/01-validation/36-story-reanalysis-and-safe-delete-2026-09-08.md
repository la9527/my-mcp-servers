# Story 재분석 및 안전 삭제 검토·구현 검증

## 결론

기존 Android 수동 Story에는 요청 스키마의 `reanalyze` 필드만 존재했고 앱이 항상 `false`를 전송했으며, Apple·Google 분석 경로가 이 값을 소비하지 않았다. Story 삭제 API도 없었다.

이번 변경은 다음 정책으로 두 기능을 연결한다.

- 수동 Story의 `다시 분석`은 원래 작업의 촬영일, 출처, 출처별 장수, 전체 장수, 6시간 제한을 복원해 새 작업으로 등록한다.
- 새 작업은 `reanalyze=true`로 Apple 처리 원장과 Google Picker의 기존 분석 제외 집합을 우회한다.
- 기존 Story는 재분석 성공 여부와 무관하게 보존한다. 새 작업이 끝나면 별도 실행 범위 Story가 만들어진다.
- `Story 삭제`는 soft delete다. 목록·상세·추천 이미지·WebView·새 교환 코드 발급에서 즉시 숨긴다.
- 삭제 시 해당 Story의 활성 30일 공유 패키지를 모두 `revoked`로 전환하고 세션 버전을 올려 기존 공유 세션도 무효화한다.
- 원본 사진, 로컬 추천 사본, 분석 이력, Apple/Google 앨범은 삭제하지 않는다. 여러 Story가 같은 추천 자산을 참조할 수 있기 때문이다.

## 사용자 흐름

```text
Story 목록
  ├─ Story 보기
  ├─ 추천 사진 보기
  ├─ 다시 분석 (직접 실행 Story만)
  │    └─ 확인 → 서명 명령 → 새 queue 작업 → 새 Story
  └─ Story 삭제
       └─ 확인 → 서명 명령 → Story 숨김 + 활성 공유 즉시 폐기
```

자동 종합 Story는 과거의 정확한 수동 요청을 복원할 수 없으므로 `다시 분석` 버튼을 표시하지 않는다. 같은 날짜를 다시 처리하려면 앱의 `날짜로 Story 만들기`에서 날짜·출처·장수를 지정한다.

## API 및 보안 계약

- `POST /mobile-client/v1/stories/{story_id}/reanalyze`
- `POST /mobile-client/v1/stories/{story_id}/delete`
- 두 요청 모두 Tailnet 소유자 확인, device-bound `curation:write` session, ECDSA owner signature, 120초 freshness, nonce replay 방어를 통과해야 한다.
- 재분석은 기존 manual queue의 idempotency 계약을 재사용한다.
- 삭제된 Story는 DB에 tombstone으로 남아 감사와 참조 안전성을 유지하지만 일반 목록에서는 제외한다.

## 검증

- 임시 SQLite 기반 단위·HTTP 통합 테스트: 전체 `845 passed`
- 추가 확인 항목:
  - 수동 Story에서 원래 범위를 복원하고 `reanalyze=true`로 만드는지
  - 이미 완료된 Apple 자산을 재분석 시 다시 제출하는지
  - 이미 처리된 Google 자산을 Picker 준비 단계에서 제외하지 않는지
  - 삭제 후 Story 목록·상세에서 보이지 않는지
  - 활성 공유가 폐기되고 session version이 증가하는지
  - 서명 없는 제어 요청이 허용되지 않는지
- Android debug/release Java·R8 빌드: 성공
- 앱 버전: `0.5.1` (`versionCode 8`)

## 운영 반영 시 주의

재분석은 동일 사진에 대해 모델 비용과 처리 시간을 다시 사용한다. Google Photos는 새 Picker 세션에서 해당 날짜 사진을 다시 선택해야 하며, 제한 장수와 6시간 상한은 기존 수동 작업과 동일하게 유지한다. 삭제는 사진 파일을 지우는 기능이 아니므로 향후 파생 파일까지 완전 삭제하는 기능이 필요하면 참조 계수와 휴지통 복구 정책을 별도 설계해야 한다.

## Galaxy Fold 실기기 최종 검증

2026-09-08에 연결된 `SM-F966N`(Android 16)에 `0.5.1`을 인플레이스 설치해 운영 상태를 확인했다.

- Story 목록에서 `Story 보기`, `추천 사진 보기`, `다시 분석`, `Story 삭제` 버튼과 직접 실행/자동 정리 구분을 확인했다.
- `다시 분석` 확인 창은 기존 날짜·출처·장수 재사용과 기존 Story 보존 정책을 안내했고, 취소 후 새 curation 작업이 생기지 않았다.
- `Story 삭제` 확인 창은 Story·공유 링크만 제거하고 원본·추천 사본·분석 이력·앨범을 유지한다고 안내했고, 취소 후 ready Story 3개가 그대로 유지됐다.
- Story WebView에서 `1 / 2`에서 `2 / 2`로 좌우 넘김, 더블 탭 `2.5×` 확대, 닫기, Android 상태 표시줄과 시스템 내비게이션 바 보존을 확인했다.
- 최초 실기기 점검에서 첫 사진을 열면 첫 카드가 `추천 사진` 텍스트만 남는 문제를 재현했다. 원인은 카드와 뷰어 캡션이 동일한 `data-title` 속성을 쓰는데 문서 전체에서 첫 요소를 선택한 것이었다. 뷰어 내부로 selector 범위를 제한해 카드 DOM 교체를 막았다.
- 수정 후 뷰어 열기 → 다음 사진으로 넘기기 → `2.5×` 확대 → 닫기를 반복했고, 두 카드가 각각 이미지와 날짜를 유지하며 `640×640` 썸네일의 로드 완료 상태를 유지했다.
- 앱 프로세스에 `FATAL EXCEPTION`은 없었고, ready Story는 3개, 활성 curation 작업은 0개였다. 확인 창은 모두 취소했으므로 실제 재분석이나 삭제는 수행하지 않았다.
- 최종 배포는 비디버그 release 빌드이며 패키지 flags에 `DEBUGGABLE`이 없다. `READ_MEDIA_IMAGES`, `ACCESS_MEDIA_LOCATION` 권한과 기존 앱 데이터도 인플레이스 설치 후 유지됐다.

최종 APK:

- 경로: `~/.photos-mcp/runtime/mobile-client/downloads/PhotosMcp-Album.apk`
- 크기: `104,721 bytes`
- SHA-256: `38cdb79961c9e54210f756985dc6094cb57590c76b488134fe3eee9e8fd67341`
- 서명 검증: APK Signature Scheme v2, v3 통과
- 회귀 테스트: `845 passed`
- Android release 빌드·lint·R8: 성공
