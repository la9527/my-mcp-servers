# mcp-my-photos

Nanobot 에서 사용하는 사진 관련 MCP 서버 묶음이다.

구성:

- `photo-source/`
- `photo-ranker/`
- `apple-terminal-helper/`

현재 Nanobot 연동 기준:

- wrapper: `/Volumes/ExtData/Nanobot/infra/scripts/run-photo-source-mcp.sh`
- wrapper: `/Volumes/ExtData/Nanobot/infra/scripts/run-photo-ranker-mcp.sh`
- installer: `/Volumes/ExtData/Nanobot/infra/scripts/install-nanobot-photo-mcps.sh`

운영 메모:

- `photo-source` 와 `photo-ranker` 는 sibling `apple-terminal-helper` 상대 경로 의존을 유지하므로, 세 디렉터리를 같은 루트 아래 형제로 둔다.
- Nanobot live gateway 는 이 루트를 기준으로 MCP 프로세스를 직접 기동한다.