# 문제 해결

문제를 “앱 실행”, “사진 접근”, “분석”, “쓰기” 중 하나로 먼저 분리하면 원인을 빠르게 찾을 수 있다.

## 1. 앱이 열리지 않거나 두 번째 실행이 거부됨

```bash
pgrep -af PhotosMcp
cat "$HOME/.photos-mcp/runtime/photos-mcp.lock"
open -a PhotosMcp
```

Photos MCP는 단일 인스턴스 잠금을 사용한다. 기존 프로세스가 실제로 실행 중이면 새 창을 중복 실행하지 않는다. 프로세스가 없는데 문제가 지속되면 앱 로그를 먼저 확인하고, 잠금 파일을 무조건 삭제하기보다 소유 프로세스와 종료 로그를 대조한다.

## 2. MCP 연결 실패

```bash
curl -v http://127.0.0.1:18791/health
lsof -nP -iTCP:18791 -sTCP:LISTEN
```

- listener가 없으면 앱 daemon 상태를 확인한다.
- health는 되지만 MCP client만 실패하면 전송 방식이 Streamable HTTP인지 확인한다.
- port를 override했다면 Nanobot URL도 같은 값인지 확인한다.

## 3. Apple 사진이 0장 또는 권한 오류

1. 앱의 환경 및 권한 화면에서 전체 검사를 실행한다.
2. macOS 시스템 설정의 개인정보 보호 및 보안에서 사진 접근을 확인한다.
3. 앨범과 날짜 범위가 실제 사진과 겹치는지 확인한다.
4. `photos_read`와 `photos_thumbnail`을 구분해 본다. 목록 조회 성공이 분석용 원본 접근 성공을 보장하지 않는다.

iCloud 전용 사진이면 즉시 분석이 차단될 수 있다. `prefetch` 또는 `wait_for_local=true`를 사용한다.

## 4. 로컬 사진 또는 ARW 미리보기가 보이지 않음

- 파일 확장자만 보지 말고 macOS ImageIO/Quick Look이 원본을 디코딩할 수 있는지 확인한다.
- 폴더의 읽기 권한과 보안 범위 접근이 유지되는지 확인한다.
- 지원되지 않는 파일이라는 빈 상태와 미리보기 생성 실패 로그를 구분한다.
- 원본은 변경하지 않으므로 캐시 문제를 의심할 때 원본 파일을 다시 저장하지 않는다.

## 5. Linux VLM이 준비되지 않음

```bash
curl -fsS http://127.0.0.1:12801/v1/models
curl -fsS http://127.0.0.1:18791/health/capabilities | python3 -m json.tool
```

첫 호출에는 워크스테이션 깨우기, SSH 연결, 모델 적재 시간이 포함될 수 있다. prepare timeout과 앱 로그의 broker 명령 결과를 확인한다. 원격 의존을 배제하려면 앱을 종료하고 `PHOTOS_MCP_VLM_POLICY=local_only`로 재실행해 비교한다.

## 6. 작업 완료인데 결과가 없거나 이전 기록만 보임

- 시작 응답의 `run_id`를 기록했는지 확인한다.
- `result_summary`와 `result_detail`을 같은 `run_id`로 조회한다.
- 0건 완료는 결과 보기 가능 상태가 아니라 빈 결과 완료로 표시돼야 한다.
- UI 갱신 문제인지 실제 DB 기록 문제인지 `jobs.db`와 앱 로그로 분리한다.

## 7. 쓰기가 반복되거나 불확실함

같은 업무를 새 요청으로 다시 보내지 않는다. 첫 요청의 승인 토큰과 mutation receipt를 보존하고, 응답이 `reconciling`이면 현재 앨범 구성과 영수증의 `confirmed_photo_ids`, `unconfirmed_photo_ids`를 확인한다. 미확인 항목은 새 계획을 만든 뒤 다시 승인한다.

## 8. 설치본만 실패

```bash
codesign --verify --deep --strict ~/Applications/PhotosMcp.app
~/Applications/PhotosMcp.app/Contents/MacOS/PhotosMcp --health
~/Applications/PhotosMcp.app/Contents/MacOS/PhotosMcp --runtime-import-smoke
~/Applications/PhotosMcp.app/Contents/MacOS/PhotosMcp --vendor-runtime-smoke
```

소스 실행이 통과하고 bundle만 실패하면 framework, 누락 dependency, codesign 순서 문제로 범위를 좁힌다.
