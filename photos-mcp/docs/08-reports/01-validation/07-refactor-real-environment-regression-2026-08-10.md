# 리팩터링 실환경 회귀 검증

## 결론

`PhotosMcp` 리팩터링 계획의 구조 변경과 기존 기능 보존 검증을 모두 완료했다. 현재 설치 앱은 Apple 사진과 로컬 파일을 읽고, JPG·HEIC·Sony ARW를 표시하며, Linux VLM으로 장기 분류하고, 승인된 결과를 로컬 디렉터리와 Apple 사진 앨범에 내보낼 수 있다.

이번 검증에서는 수정이 필요한 소스 결함이 발견되지 않았다. 따라서 남은 항목은 리팩터링 미완료가 아니라 추천 품질의 사람 평가와 Google Photos 실제 계정 연동 같은 후속 기능이다.

## 검증 환경

| 항목 | 값 |
| --- | --- |
| macOS 앱 | `/Volumes/ExtData/02_Services/PhotosMcp/PhotosMcp.app` |
| 앱 상태 | `ready`, 활성 작업 0, 대기 중 쓰기 0 |
| 앱 서명 | `codesign --verify --deep --strict` 통과 |
| VLM 서버 | Linux workstation, `Qwen3.6-35B-A3B-Q4_K_M.gguf` |
| VLM 컨텍스트 | 200,000 토큰 |
| 자동 종료 보호 | 분류 중 activity 갱신 확인, 검증 후 idle watchdog 재활성화 |

기존 설치본은 `/Volumes/ExtData/system/Applications/PhotosMcp.pre-refactor-20260810-070236.app`에 보존했다.

## 설치 앱 기능 검증

Computer Use로 설치 앱을 직접 조작해 다음 항목을 확인했다.

| 기능 | 결과 |
| --- | --- |
| 메인·환경 및 권한·사진 분류·작업 기록 화면 | 통과 |
| Apple 사진 권한·읽기·미리보기·앨범 자동화 사전 검사 | 모두 통과 |
| 로컬 폴더 트리, 격자·한 장 보기, 선택 체크 | 통과 |
| JPG 7008×4672, HEIC 1920×1080, Sony ARW 7008×4672 미리보기 | 통과 |
| 한 장 보기 좌우 이동, `Space`·`Return` 선택 전환 | 통과 |
| 전체 화면 전환과 창 크기 변경 시 재배치 | 겹침 없이 통과 |
| 1,000장 결과 갤러리, 필터, inspector, 스크롤 | 통과 |

초기 Apple 자동화 검사에서 Photos 프로세스가 일시적으로 Apple Event에 응답하지 않아 한 차례 시간 초과가 발생했다. Photos를 정상 종료 후 다시 열자 읽기 전용 앨범 검사와 앱 사전 검사가 통과했으며, Photos 외부 프로세스의 일시 정지로 판정했다.

## 1,000장 혼합 분류

입력은 원본을 변경하지 않는 hardlink 기반 검증 세트로 준비했다.

| 항목 | 결과 |
| --- | ---: |
| 입력 | 1,000장, 약 33GB |
| 형식 | ARW 858장, JPG 141장, HEIC 1장 |
| 전체 처리 시간 | 4,598.45초, 76분 38초 |
| 원본 로드 | 43.79초 |
| 1차 필터 | 43.92초 |
| 중복 분석 | 2.98초 |
| Linux VLM | 4,549.06초, 상세 대상 817/817 |
| 중복 감지 | 264장 |
| 장면 군집 | 598개, 이 중 복수 사진 군집 140개 |
| 추천 | 262장 |
| 검토 필요 | 738장 |

추천 정책은 `relative_scene_top_2`다. 장면별 1순위 140장과 2순위 122장을 선별했고, 한 장면에서 추천이 2장을 넘은 경우는 없었다. 결과 화면도 전체 1,000장, 추천 262장, 검토 필요 738장을 동일하게 표시했다.

동일 시스템의 직전 500장 검증은 VLM 상세 사진당 평균 5.752초였고 이번 검증은 5.568초였다. 데이터 구성이 완전히 같지는 않지만 동일 실행 경로 기준 약 3.2% 빨라져 10% 이상의 성능 퇴행은 관찰되지 않았다.

## 메모리 계측

428개 시점의 런타임 표본을 기록했다.

| 대상 | 최대 또는 최저 값 |
| --- | ---: |
| macOS PhotosMcp RSS 최대 | 0.59GiB |
| macOS E2E 실행 프로세스 RSS 최대 | 2.23GiB |
| Linux llama-server memory peak | 28.41GiB |
| AMD GPU VRAM 사용 최대 | 24.09GiB |
| Linux MemAvailable 최저 | 49.78GiB |
| 동시 VLM slot | 1개 중 최대 1개 |

장기 분류 중 요청 activity가 계속 갱신되어 30분 유휴 종료 조건이 잘못 발동하지 않았다. 종료 후 Linux의 `nanobot-llm-idle-watch.service`와 `llama-server.service`가 모두 `active`임을 확인했다.

## 내보내기와 상태 복구

| 검사 | 결과 |
| --- | --- |
| 선택 원본 내보내기 | 미디어 1,000개, XMP 1,000개, 누락 0 |
| 분류 디렉터리 | 추천 262개, 검토 필요 738개 |
| 같은 요청 재실행 | 완료 영수증 재사용, `duplicate_suppressed=true` |
| Apple 사진 실제 쓰기 | 새 검증 앨범 1개, 기존 사진 1장 추가, 실패 0 |
| Apple 쓰기 중복 요청 | 추가 쓰기 없이 억제 통과 |
| Apple 읽기 전용 재확인 | 검증 앨범 1개와 사진 1장 확인 |
| 실행 중 재시작 복구 | `awaiting_resume_approval`, 같은 run 재개 계획 확인 |
| 사용자 재개 승인 | 대기 작업으로 전환, 기존 run id 유지 |

사진 식별자, 인물 정보, 앨범 내부 UUID와 분석 원문은 개인정보 보호를 위해 이 보고서에 기록하지 않았다.

## 자동 회귀와 번들

```bash
PYTHONPATH=src .venv/bin/pytest -q
PYTHONPATH=src .venv/bin/python scripts/validate_docs.py
./scripts/build_framework_standalone.sh
```

| 검사 | 결과 |
| --- | --- |
| 전체 테스트 | `466 passed in 4.92s` |
| 내보내기·재개·쓰기 안전·상태·상대 추천 집중 검사 | `26 passed in 1.42s` |
| standalone 빌드 | 통과 |
| 번들 health | 통과 |
| 번들 runtime import smoke | 통과 |
| 번들 vendor runtime smoke | 통과 |

## 남은 위험과 후속 범위

- 추천 262장이 사용자 취향과 정성적으로 일치하는지는 별도 사람 검토 데이터로 측정해야 한다. 이번 검증은 장면당 최대 2장 계약과 처리 안정성을 확인했다.
- 1,000장 로컬 세트에는 iCloud 원본만 존재하는 항목을 의도적으로 섞지 않았다. 대신 Apple 사진 읽기·미리보기·자동화와 Apple 원본 1장 실제 분류·앨범 쓰기를 별도로 통과했다.
- Google Photos는 Picker 경계와 가짜 수명주기 테스트까지만 구현되어 있다. 실제 Google 계정 OAuth·Picker·만료 URL·재개 검증은 신규 기능 단계에서 수행한다.

이 세 항목은 현행 리팩터링의 완료를 막는 결함이 아니라 다음 제품 단계의 품질·cloud 연동 작업이다.
