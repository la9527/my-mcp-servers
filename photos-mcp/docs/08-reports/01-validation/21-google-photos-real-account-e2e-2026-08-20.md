# 2026-08-20 Google Photos 실계정 E2E 검증

## 범위

기존 macOS Keychain OAuth 연결을 사용해 Google Photos Picker에서 이미 확정한 선택 항목을 실제 분류하고, 사용자가 명시 승인한 추천 결과를 새 app-created album으로 업로드했다. credential, picker URL, Google media ID, 사진 파일명과 위치 정보는 기록하지 않는다.

## 결과

| 단계 | 결과 |
| --- | --- |
| OAuth 설정과 refresh token | Keychain 설정 확인 및 access token 갱신 성공 |
| Picker 선택·다운로드 | 사진 56장 다운로드 완료, 동영상 1개 제외 |
| Google 입력 분류 | 최대 50장 설정으로 50장 완료, 실패 0건 |
| 분석 runtime | `linux-workstation-lan` 직접 SSH tunnel과 Linux Qwen3.8 API 연결 성공 |
| 결과 | 추천 30장, 검토 필요 20장, 장면 15개 |
| 새 album 업로드 | 명시 승인 뒤 추천 30장, 85,595,488 bytes 업로드 성공, 실패 0건 |
| Google UI 확인 | `Photos MCP - 73dfd063 추천` 새 album과 업로드된 30개 새 사본 표시 확인 |

## 관찰 사항

- 이번 검증은 사용자의 명시적인 실행 지시에 따라 Google 선택 사진을 직접 LAN의 Linux Qwen3.8 runtime으로 전송해 분석했다.
- 개인 단독 사용자가 Mac과 동일 LAN의 Linux workstation을 직접 관리하는 현재 설치에서는 별도 전송 consent sheet를 요구하지 않는다. Google OAuth와 Picker에서 사용자가 직접 선택하는 경계는 유지하며, 다중 사용자·원격 runtime 배포 시에는 별도 전송 고지와 동의를 다시 도입한다.

## 원본 보호 확인

- Picker로 선택한 기존 Google Photos 항목을 수정·이동·삭제하는 API 경로는 호출하지 않았다.
- Library API에는 명시 승인된 로컬 temporary bytes를 새 media item으로 올리고, 새 app-created album에만 추가했다.
- Google Photos UI에서 기존 원본을 다시 구성한 흔적 없이 별도 새 album이 열리는 것을 확인했다.

## 남은 실계정 예외 검증

- 최초 OAuth 동의가 없는 새 Keychain 상태에서의 loopback callback
- 1장 및 10장 Picker 선택, Picker 취소, 선택 timeout
- refresh token 철회·만료 뒤 재연결
- 업로드 중 네트워크 단절·부분 실패 후 재개와 중복 방지
