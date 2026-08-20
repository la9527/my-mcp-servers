# 2026-08-20 Linux Qwen3.8 VLM 기본값·실사진 검증

## 변경

- PhotosMcp Linux VLM 기본 모델을 `Qwen3.8-27B-Q4_K_M.gguf`로 변경했다.
- 환경 검사와 상태 메뉴의 사용자 표기를 `Linux Qwen3.8` 및 `Qwen3.8-27B`로 통일했다.
- 기존 작업 결과와 설정 호환성을 위해 내부 provider `linux_qwen36` 및 target `linux-qwen36-vlm` 이름은 유지한다.

## 실환경 확인

| 항목 | 결과 |
| --- | --- |
| Linux 모델 endpoint | `/v1/models`가 `Qwen3.8-27B-Q4_K_M.gguf`를 multimodal 모델로 반환 |
| 연결 경로 | `linux-workstation-lan` 직접 SSH tunnel, `127.0.0.1:12801/v1` |
| 실사진 1장 VLM 요청 | 정상 JSON 응답 |
| 응답 구조 | event type, 신뢰도, 의미 점수, 인물 수 모두 파싱 성공 |
| 자동 테스트 | `618 passed` |
| standalone 번들 | codesign, health, runtime import, vendor runtime smoke 통과 |

사진 원본과 장면 설명 등 개인 데이터는 이 문서에 기록하지 않았다.
