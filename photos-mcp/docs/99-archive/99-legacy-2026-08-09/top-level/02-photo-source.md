# photo-source

## 1. 역할

`photo-source` 는 사진을 어디서 읽어 올지 담당하는 조회 계층이다. `photos-mcp` 안에서는 내부 vendor runtime 이지만, 개념적으로는 독립된 source adapter 집합으로 이해하는 것이 맞다.

외부 MCP client 는 `photos-mcp` 하나만 보지만, 실제로 아래 요청들은 대부분 `photo-source` 가 처리한다.

- 사진 목록 조회
- 메타데이터 조회
- thumbnail 생성
- 검색
- export

## 2. 지원 source

현재 code 기준 지원 source 는 아래 네 가지다.

- `apple`: Apple Photos library
- `local`: 로컬 디렉터리
- `google`: Google Photos
- `gcs`: Google Cloud Storage bucket

실무 기준으로 가장 중요한 경로는 `apple` 이다. `photos-mcp` 가 macOS app 으로 존재하는 이유도 결국 Apple Photos read/write 와 permission 흐름을 다루기 위해서다.

## 3. 노출 tool

`photo-source` 가 제공하는 tool 은 6개다.

### `list_photos`

역할:

- source 에서 사진 목록을 읽는다.
- 날짜, 앨범, 인물, limit 같은 필터를 적용한다.

주요 인자:

- `source`: `local`, `apple`, `google`, `gcs`
- `path_or_bucket`: local 디렉터리 또는 gcs bucket
- `date_from`, `date_to`
- `album`
- `person`
- `limit`

### `get_metadata`

역할:

- 특정 사진의 상세 메타데이터를 읽는다.

주요 인자:

- `source`
- `photo_id`
- `path_or_bucket`

### `get_thumbnail`

역할:

- 특정 사진의 resized thumbnail 을 base64 문자열로 반환한다.

주요 인자:

- `source`
- `photo_id`
- `path_or_bucket`
- `max_size`

### `search_photos`

역할:

- 키워드로 사진을 검색한다.

현재 제약:

- `apple`, `google` 만 지원한다.

### `export_photos`

역할:

- 지정한 photo id 목록을 출력 디렉터리로 내보낸다.

주요 인자:

- `source`
- `photo_ids`
- `output_dir`
- `path_or_bucket`
- `max_size`

### `prefetch_photos`

역할:

- Apple Photos 원본을 선별/분석 전에 미리 로컬로 확보한다.
- 이미 로컬에 있는 자산과 새로 내려받은 자산, 실패한 자산을 분리해 반환한다.

주요 인자:

- `source`
- `photo_ids`
- `date_from`, `date_to`
- `album`
- `person`
- `limit`

## 4. `photos-mcp` 안에서 어떻게 쓰이는가

`photos-mcp` 는 `photo-source` 를 두 방식으로 사용한다.

### 직접 조회 tool 로 사용

예:

- `list_photos`
- `get_thumbnail`
- `export_photos`
- `prefetch_photos`

이 경우 MCP client 요청이 거의 그대로 `photo-source` 로 내려간다.

### `photo-ranker` 의 하위 입력 계층으로 사용

예:

- `start_classify_job`
- `classify_and_organize`
- `curate_best_photos`

이 경우 `photo-ranker` 가 실제 사진을 읽기 위해 내부적으로 `photo-source` 계열 source adapter 를 다시 사용한다.

즉, `photo-source` 는 사용자에게 직접 노출되는 동시에, `photo-ranker` 가 의존하는 기반 계층이기도 하다.

## 5. 대표 흐름

가장 기본적인 흐름은 아래와 같다.

1. `list_photos(source="apple", album="최근")`
2. 원하는 `photo_id` 선택
3. `get_metadata(source="apple", photo_id=...)`
4. `get_thumbnail(source="apple", photo_id=...)`
5. thumbnail base64 를 `photo-ranker` 분석 tool 에 전달

이 흐름이 중요한 이유는 `photo-source` 와 `photo-ranker` 가 어떻게 연결되는지 가장 직관적으로 보여주기 때문이다.

## 6. Apple Photos 경로에서 중요한 점

Apple Photos source 는 단순 파일 시스템 접근이 아니라 macOS Photos library 와 permission 모델에 걸쳐 있다. 그래서 아래 조건이 중요하다.

- Photos library read 가 가능한지
- 필요한 경우 Automation permission 이 허용되었는지
- bundle/source 실행 환경에서 vendor import 가 정상인지
- helper subprocess 가 올바른 Python 을 사용하고 있는지

이 readiness 는 `/health/capabilities` 와 preflight 결과에서 함께 판단해야 한다.

## 7. direct mode 와 terminal helper

Apple Photos 관련 조회는 상황에 따라 direct mode 와 Terminal helper mode 를 사용할 수 있다. 이 구분은 source tree 에서 잘 되던 동작이 bundle 에서만 깨지는 문제를 추적할 때 중요하다.

핵심 포인트:

- direct mode 는 현재 프로세스에서 직접 접근한다.
- helper mode 는 별도 subprocess 또는 Terminal 경유 실행을 사용한다.
- child 쪽에서는 다시 direct mode 로 내려가게 만드는 보호 로직이 들어간다.

구현 세부 사항은 `12-runtime-lifecycle.md` 와 `14-debugging-guide.md` 를 함께 본다.

## 8. 언제 `photo-source` 문서를 먼저 봐야 하는가

아래 상황이면 이 문서를 먼저 보는 것이 맞다.

- 사진이 아예 안 보인다.
- Apple Photos 조회는 되는데 분석이 안 된다.
- local/GCS/Google source 별 차이를 이해하고 싶다.
- `photo_id` 와 source path/bucket 인자 의미가 헷갈린다.
- export 결과가 기대와 다르다.

`photo-source` 는 분석 전에 데이터를 꺼내 오는 층이다. 따라서 문제를 좁힐 때도 먼저 “사진을 제대로 읽고 있는가”를 분리해 주는 기준점이 된다.