# 예외 중심 인물 인식 Mac/Android 구현 검증

검증일: 2026-09-12 KST

대상: PhotosMcp private identity schema v6, exception-only 얼굴 정책, macOS AppKit 인물 관리, Android Companion 0.8.3, Story 인물·지도 projection

## 1. 검증 결론

모든 검출 얼굴을 사람이 하나씩 정리하던 화면을 **애매한 얼굴과 반복 등장한 새 사람만 확인하는 구조**로 전환했다. 얼굴 품질 미달과 단발·2회 관측 얼굴은 삭제하지 않고 숨기며, 새 사람은 서로 다른 사진에서 3회 이상 확인된 경우에만 한 건으로 보여준다.

macOS와 Android는 동일한 `PeopleWorkspaceService`, identity 원장과 signed command를 사용한다. Android 기기에 별도 인물 상태를 복제하지 않으므로 한쪽에서 확정한 이름·Story 동의·자동 인식 설정은 다음 조회에서 다른 쪽에도 동일하게 보인다.

## 2. 구현 범위

| 영역 | 반영 결과 |
|---|---|
| 얼굴 품질 | detector, crop 크기, 선명도, 노출, frontal, 잘림, screenshot을 버전 평가 |
| 새 인물 | 1~2회 관측 숨김, 3 asset·2 context 이상만 묶음 검토 |
| 묶음 확정 | 대표 얼굴에서 이름 한 번 입력 시 적격 cluster 전체를 owner anchor로 확정 |
| 기존 인물 | 3장부터 확인 가능, 5장·3문맥부터 초고신뢰 자동 연결 가능 |
| 자동 결과 | `auto_accepted`로 분리 저장, Story 사용 가능, 학습 anchor에서는 제외 |
| 예외 큐 | 빠른 확인·두 후보 비교·반복 새 인물만 조회; legacy singleton 제외 |
| macOS | 빠른 확인 CTA, 얼굴/근거 crop, 자동 인식 토글, Tailnet Story URL |
| Android | 얼굴 중심 Activity, 다인물 전환, 근거 3장, 대표 얼굴 인물 선택, 이름·무시·나중에, 자동 인식 토글 |
| 보안 | private crop은 인증된 만료 handle 경로, 변경은 서명·nonce·revision·idempotency 적용 |

## 3. 회귀·빌드 검증

| 검증 | 결과 |
|---|---|
| Python 전체 테스트 | 1,039건 통과, 15.70초 |
| 문서 검증 | Markdown 108개 통과 |
| Android debug compile/lint | `assembleDebug`, `lintDebug` 성공 |
| Android release compile/lint | `assembleRelease`, `lintRelease`, R8/resource shrink 성공 |
| APK 서명 | v2·v3 서명 검증 성공, signer 1 |
| APK | 0.8.3, versionCode 22, 129,353 bytes |
| APK SHA-256 | `aba164da1d3668806cd7f8df70994dbe77d972433b136d422f36adfc614ca3f0` |
| Tailnet APK 다운로드 | HTTP 200, 129,353 bytes |
| macOS standalone | ad-hoc deep codesign, health, runtime import, vendor runtime, person runtime smoke 성공 |

## 4. 실제 운영 데이터 호환성

운영 identity DB를 schema v6으로 additive migration했다. 확인 당시 집계는 다음과 같다.

- 사용자 확정 인물: 3명
- 최신 automation profile: 3개
- `auto_ready`: 3명
- 구형 pending singleton: 67건 보존
- 현재 정책의 실제 예외: 0건

구형 67건은 삭제하지 않았다. 새 정책에서 근거가 부족한 단발 얼굴이므로 홈·빠른 확인에서만 제외했다. 향후 다른 날짜의 고품질 관측과 묶이면 기존 observation을 재사용할 수 있다.

## 5. Android 실기기 검증

대상은 USB 연결된 Samsung SM-F966N(Android 16, 1080×2520)이다.

1. `adb install -r`로 0.8.3을 설치해 기존 기기 등록·설정·GPS outbox를 보존했다.
2. package metadata에서 versionName `0.8.3`, versionCode `22`를 확인했다.
3. 홈 → 설정 → 인물 관리로 진입했다.
4. 서버 재시작 전의 0.8.2 응답 불일치를 발견해 `com.photosmcp.mobile-client` launchd 서비스만 재시작했다.
5. 이후 서버 capability가 0.8.3과 `people_automatic_recognition=true`를 반환했다.
6. 인물 관리 홈에서 legacy 67건 대신 `지금 확인할 내용이 없어요`가 표시됐다.
7. 인물 카드에서 연결 사진 11장·6장인 기존 인물의 `자동 인식 · 안정됨` 토글을 확인했다.
8. app process가 유지됐고 Android crash buffer에서 앱 관련 fatal exception은 없었다.

사용자 이름·얼굴 crop 자체는 보고서에 복사하지 않았다.

## 6. Google 지도 경로 검증

macOS 내장 Story가 `http://127.0.0.1:18791/photos`를 직접 열면 Embed 키의 referrer 제한에 의해 403이 발생했다. 내장 Story URL을 `PHOTOS_MCP_OWNER_STORY_URL`의 Tailnet HTTPS 주소로 통일했다.

검증 결과:

- `https://byoungyoung-macmini.tail53bcc7.ts.net/photos`: HTTP 200
- 현재 Story: 지도 iframe 포함, 사진 24장
- 동일 Maps Embed 요청 + Tailnet referrer: HTTP 200, 거부 문구 없음
- 동일 요청 + loopback referrer: HTTP 403, referrer 거부 확인

따라서 API 키 제한을 느슨하게 만들지 않고 운영 허용 origin을 사용하는 방식으로 해결했다. 외부 브라우저 fallback인 `Google 지도에서 열기`도 유지한다.

## 7. 개인정보·오염 방지 확인

- embedding, 내부 identity/face ID, 파일 경로, raw similarity는 Android JSON과 Story에 노출하지 않는다.
- 검토 crop은 owner session과 만료 handle이 있어야 읽을 수 있고 `no-store, private`로 반환한다.
- Android 변경 요청은 ECDSA 서명, nonce, idempotency key와 expected revision을 검증한다.
- 자동 연결은 owner anchor가 아니며, 자동 결과를 다시 학습시켜 오인식이 증폭되는 경로를 테스트로 차단했다.
- 이름과 인물 DB는 Story/작업 기록 삭제와 분리되어 유지된다.
- 품질 제외와 legacy singleton 숨김은 데이터 삭제가 아니며 정책 version을 바꿔 재평가할 수 있다.

## 8. 운영 관찰 gate

코드·배포 작업은 완료했지만 실제 사용자 판단 없이는 완료할 수 없는 품질 gate는 다음과 같다.

- 새 분석의 자동 연결 오인식 및 교정률 측정
- 서로 다른 날짜의 셋째 관측에서 새 인물 한 건으로 승격되는지 확인
- 모델 fingerprint 변경 시 profile 재검증 확인
- 사용자 교정 후 동일 오인식 재발 여부 확인

이 결과를 모으기 전에는 자동 임계값을 낮추거나 자동 결과를 owner anchor로 바꾸지 않는다.

## 9. macOS 인물 홈 시각 구조 보완

최초 배포본은 예외 중심 정책과 `빠르게 확인` 동작을 기존의 좌측 목록·우측 편집 패널에 연결했다. 기능은 최신이었지만 화면 골격이 이전 버전과 같았고, 안정 인물 원장에 대표 얼굴이 있어도 최근 작업 캐시에 얼굴이 없으면 `사진 연결 필요`라고 표시했다. 사용자는 정책 변경을 알아보기 어렵고 인물의 실제 얼굴도 확인할 수 없었다.

후속 보완에서는 다음과 같이 수정했다.

- 인물 관리 첫 화면을 `확인할 내용` 카드와 `관리 중인 인물` 대표 얼굴 카드 grid로 재구성했다.
- 내부 검출량·품질 제외량·lineage 대기 수를 첫 화면에서 제거하고 사용자가 처리할 예외 수만 우선 표시한다.
- 안정 인물 DB의 `representative_face_ref`, 연결 사진 수, 직접 확인 anchor 수, 자동 인식 성숙도를 macOS 카드에 직접 연결한다.
- 인물 카드를 선택해야 이름·자동 인식·Story 공개 범위와 고급 얼굴 정리 화면으로 이동한다.
- Apple Photos 이름 후보도 홈의 확인 수에 합쳐 `빠르게 확인하기` 한 동선에서 처리한다.
- 상세 화면에는 `← 인물 목록`을 제공해 현재 위치와 복귀 동작을 명확히 했다.
- 기존 얼굴 이동, 병합, 분리, 얼굴 아님, 실행 취소 기능은 상세 화면에 그대로 보존했다.

실제 운영 identity DB의 관리 인물 3명 모두에서 대표 crop 파일 존재와 사진 수가 확인됐다. 설치 앱 화면에서 세 대표 얼굴 카드, 이름, 사진 수, 직접 확인 수, 자동 인식 상태를 확인했으며 로컬 health는 `status=ok`, `daemon_status=ready`를 반환했다.
