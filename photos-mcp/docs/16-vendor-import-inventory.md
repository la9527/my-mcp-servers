# vendor import inventory

이 문서는 Phase 1 의 첫 기준선이다. 목표는 `photo-source` 와 `photo-ranker` 의 top-level local import 를 먼저 고정한 뒤, package namespace 전환 범위를 작게 나누는 것이다.

## 1. 현재 결론

현재 vendor runtime 과 직접 실행용 CLI/script/review app 은 더 이상 `models`, `sources`, `db`, `jobs`, `pipeline`, `engines` 같은 vendor-local module 을 top-level 로 import 하지 않는다.

`photo-source` 와 `photo-ranker` 는 `photos_mcp_vendor_photo_source`, `photos_mcp_vendor_photo_ranker` package alias 아래에서 로드된다. 직접 실행용 script 는 `_script_bootstrap.py` 를 통해 같은 alias 를 준비한 뒤 명시적 package import 를 사용한다.

## 2. 충돌 위험이 가장 큰 이름

- `models`: `photo-source/models.py` 와 `photo-ranker/models.py` 가 같은 top-level 이름을 공유한다.
- `sources`: `photo-source/sources/` package 와 `photo-ranker/sources.py` module 이 같은 top-level 이름을 공유한다.

이 두 이름은 Phase 1 에서 package namespace 로 닫았다. 새 코드에서 이 이름을 top-level import 로 되살리면 `tests/test_vendor_import_inventory.py` 가 실패해야 한다.

## 3. photo-source top-level local import

기준 root: `src/photos_mcp/vendor/photo-source`

운영 runtime 과 직접 실행용 script 모두 package namespace 로 전환했다.

| 파일 | top-level local import |
| --- | --- |
| 없음 | 없음 |

## 4. photo-ranker top-level local import

기준 root: `src/photos_mcp/vendor/photo-ranker`

운영 MCP server 경로와 직접 실행용 CLI/script/review app 모두 package namespace 로 전환했다.

| 파일 | top-level local import |
| --- | --- |
| 없음 | 없음 |

## 5. 전환 순서

권장 순서는 아래와 같다.

1. 완료: `photo-source` 의 `models` / `sources` 를 package-relative import 로 전환했다.
2. 완료: `photo-ranker` 의 `models` / `sources` 를 package namespace 아래로 전환했다.
3. 완료: `photo-ranker` 내부 모듈(`db`, `jobs`, `pipeline`, `engines`, `scoring` 등)을 package-relative import 로 전환했다.
4. 완료: `server.py` 를 package alias 아래에서 로드하도록 전환했다.
5. 완료: `vendor_loader.py` 에서 `sys.modules` 삭제와 vendor root `sys.path` 삽입이 필요 없도록 했다.

## 6. 테스트 기준

`tests/test_vendor_import_inventory.py` 는 이 문서의 목록을 기준선으로 고정한다. 현재 기준선은 빈 목록이다. 새 top-level local import 가 생기면 테스트가 실패해야 한다.

의도적으로 import 표면을 바꾸는 경우에는 아래를 함께 갱신한다.

- `docs/16-vendor-import-inventory.md`
- `tests/test_vendor_import_inventory.py`
- `docs/15-refactor-direction.md` 의 Phase 1 checkbox / 완료 메모
