# 이미 확정된 얼굴의 Apple Photos 이름 후보 완료 검증

## 결론

한 사진의 얼굴 두 명을 각각 인물에 확정했지만 Apple Photos 이름 후보가 `candidate`로 남아, 상세 화면에서 `사진 속 얼굴 0개`와 비활성 버튼만 표시되던 상태를 수정했다. 얼굴을 다시 검출하거나 기존 membership을 바꾸지 않고, 사진 안에서 이름 후보와 확정 인물이 일대일로만 대응될 때 이름 후보를 한 번에 완료한다.

운영 문제 사진에서는 이름 후보 2건이 서로 다른 확정 인물 2명에게 각각 유일하게 대응됐다. 새 완료 경로를 적용한 뒤 두 얼굴은 기존 인물 연결을 유지하고, 두 provider alias만 `owner_confirmed`로 전환됐다. 사진 원본, 실제 인물 이름, 내부 식별자는 이 보고서에 기록하지 않는다.

## 원인

- 얼굴별 빠른 확인은 `face_observation → person_identity` membership을 확정했다.
- 당시 명령에는 같은 사진의 `alias_id`가 포함되지 않아 Apple Photos 이름 후보 상태는 별도로 남았다.
- 이름 후보 상세 projection은 owner-confirmed 얼굴을 다시 선택하지 못하게 숨기므로 선택 가능한 얼굴은 0개가 됐다.
- 기존 안내인 `이 사진 얼굴 다시 찾기`는 검출을 반복할 뿐 두 저장 상태를 결합하지 못했다.

## 적용한 동작

### 공유 application service

- 확정 얼굴을 `resolved_faces`로 별도 투영한다.
- 이름 공백을 제거한 뒤 동일 이름 또는 전체 이름의 끝부분과 일치하는지 검사한다.
- 한 글자 이름, 한 후보가 여러 인물에 맞는 경우, 여러 후보가 같은 얼굴·인물에 맞는 경우는 자동 완료 대상에서 제외한다.
- 모든 대기 후보가 서로 다른 확정 얼굴·인물에 일대일로 대응될 때만 `can_complete_resolved_aliases`를 활성화한다.
- 앞으로 일반 얼굴 확인 명령에 `alias_id`가 빠졌더라도 이름이 유일하게 대응되면 같은 atomic review transaction에 provider alias를 포함한다.

### macOS 앱

- `선택할 얼굴이 없습니다` 대신 확정된 얼굴 crop과 이름을 표시한다.
- `Apple 이름 → PhotosMcp 인물 이름` 관계를 한 줄로 보여준다.
- `이 사진 이름 후보 N건 완료` 버튼으로 한 번에 마무리한다.
- 이 상태에서는 도움이 되지 않는 `이 사진 얼굴 다시 찾기` 버튼을 숨긴다.
- 완료 작업은 얼굴 membership을 새로 만들지 않고 기존 연결을 검증한 뒤 alias assignment와 alias revision만 확정한다.

### Android 0.8.4

- 인증된 얼굴 검토 API가 이미 확정된 얼굴의 복구 항목도 opaque action handle로 제공한다.
- 내부 asset id, face id, person id, private 경로는 응답하지 않는다.
- Android의 `빠르게 인물 확인` 화면에서도 확정 crop과 이름 관계를 확인하고 `이 사진 이름 후보 N건 완료`를 실행할 수 있다.
- 얼굴 검토 API의 자동 인식 profile도 허용된 상태 필드만 반환하도록 제한해 내부 person id가 중첩 객체에서 노출되지 않게 했다.

## 운영 데이터 검증

| 항목 | 수정 전 | 수정 후 |
| --- | ---: | ---: |
| 검출 얼굴 | 2 | 2 |
| 확정 얼굴 membership | 2 | 2 |
| Apple Photos 대기 이름 후보 | 2 | 0 |
| 이름 후보 완료 | 0 | 2 |
| asset review 상태 | completed | completed |
| 얼굴 index 상태 | completed | completed |
| 감사 체인 | 정상 | 정상 |

기존 인물 연결은 그대로 유지됐고 얼굴별 review state도 둘 다 `resolved`다.

## 검증

- 전체 Python 테스트: `1050 passed`
- 저장소 집중 테스트: `43 passed`
- mobile-client API 테스트: `25 passed`
- AppKit layout 테스트: `104 passed`
- Android debug compile·assemble: 통과
- Android 0.8.4 release assemble·lint·서명 검증: 통과
- 배포 APK SHA-256: `d145410ebc64782be95f14335676870ed1930399e4bd86cbcb519f7b72675d0d`
- macOS standalone 빌드·strict code signing·health·runtime smoke: 통과
- 설치 앱 재시작 후 `/health`: `daemon_status=ready`
- 운영 private identity 감사 체인 검증: 통과

## 회귀 방지 규칙

1. 얼굴 인식 결과와 provider 이름 후보는 별도 상태이지만, 유일한 이름 대응이 있으면 같은 명령에서 함께 확정한다.
2. 이미 확정된 얼굴은 재검출 대상으로 안내하지 않는다.
3. 자동 이름 결합은 사진 단위 일대일 대응일 때만 허용한다.
4. 애매한 이름은 기존 crop 선택 흐름에 남기고 자동 처리하지 않는다.
5. 클라이언트에는 private repository 식별자 대신 짧은 수명의 기기별 action handle만 제공한다.
