# 보안과 개인정보

> 근거: `mutation_approval.py`, `mutation_plan_service.py`, `preflight.py`, `vision_runtime.py`, `selected_export_bundle`

## 기본 위협 모델

Photos MCP는 개인 사진, 얼굴, 촬영 시각과 로컬 파일 경로를 다룬다. 따라서 편리한 자동 분류보다 다음 경계를 우선한다.

- 원본 변경 방지
- 사용자 승인 없는 쓰기 방지
- 민감 식별자의 불필요한 외부 전송 방지
- 로컬 경로와 내부 오류의 UI 노출 최소화
- 작업 재시도 시 중복 변경 방지

## 읽기 전용 분석

사진 목록, thumbnail, 메타데이터 조회와 분석은 원본 또는 Apple 사진 앨범을 변경하지 않는다. 로컬 폴더 분류 UI도 source 파일을 읽기만 한다.

## 쓰기 승인

`photos_write`와 쓰기를 포함하는 workflow는 첫 호출에서 계획만 만든다. token은 다음 특성을 가진다.

- options와 실제 대상의 fingerprint에 결합
- 제한된 유효 시간
- 한 번만 소비
- 변경된 요청에 재사용 불가

쓰기 결과는 영수증에 기록되며 부분 실패와 timeout 뒤 실제 대상을 다시 조회해 중복 적용을 막는다.

## VLM 정책

기본 VLM은 loopback OpenAI-compatible endpoint를 통해 Linux 워크스테이션으로 연결될 수 있다. 원격 시스템 전송을 허용하지 않으려면 다음 정책을 사용한다.

```bash
PHOTOS_MCP_VLM_POLICY=local_only /Applications/PhotosMcp.app/Contents/MacOS/PhotosMcp
```

이 정책에서는 Mac 로컬 VLM만 선택한다. 현재 정책과 provider는 capabilities payload와 앱 환경 화면에서 확인한다.

## 얼굴 데이터

얼굴 관련 테이블은 로컬 runtime DB에 존재한다. 얼굴 crop이나 임베딩을 외부 문서·보고서에 포함하지 않는다. 개인 검증 데이터와 공개 벤치마크를 분리하며, 저장된 얼굴 정보를 제품 기능으로 사용할 때는 사용자 확인 단계를 유지한다.

## 로그와 보고서

- 로그에는 API key, 승인 token과 원본 전체 payload를 남기지 않는다.
- 공개 보고서에는 개인 사진, photo ID, 절대 원본 경로를 포함하지 않는다.
- 검증용 private annotation은 `.runtime` 등 Git 제외 경로에 둔다.

## macOS 권한

Apple 사진 접근은 TCC 권한을 따른다. preflight는 읽기, automation, thumbnail 기능을 구분한다. 선택 검사는 사용자가 기능을 요청할 때 실행해 불필요한 권한 프롬프트를 줄인다.
