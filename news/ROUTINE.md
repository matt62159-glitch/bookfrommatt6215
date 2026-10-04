# 아침 뉴스 노트 수집 루틴 (Claude Code 세션용 지시서)

이 파일은 수집 루틴이 실행될 때 Claude가 따르는 순서다. 루틴 프롬프트는 "news/ROUTINE.md를 읽고 그대로 수행"이면 충분하다.
루틴이 받은 추가 메시지(JSON: `reason`, `hours`, `perOutlet`)가 있으면 참고한다.

- 앱: https://claude.ai/artifact/8CXzcr6VTcg7Cfs6nD6G3E (ArtifactData의 `url`)
- 개인 영역: `data/users/me` (본인만 읽고 쓰는 경로)
- 커밋·푸시하지 않는다. 결과는 앱 저장소에만 쓴다.

## 1. 설정 읽기
`ArtifactData get` collection `data/users/me`, doc_id `settings`. 없으면 hours=24, perOutlet=5.

## 2. 기사 목록과 본문 수집
```
python3 news/collector/collect.py --hours <hours> --per-outlet <perOutlet> --out /tmp/news-out
```
- `/tmp/news-out/articles/*.json` 이 기사 문서, `/tmp/news-out/status.json` 이 매체별 성공·실패 상태다.
- 수집기가 실패한 매체를 가짜 기사로 채우지 않는다. 실패는 status에 그대로 남긴다.

## 3. 직접 추가한 기사의 본문 확보
`ArtifactData query` collection `data/users/me/news/articles`, where `["bodyStatus","==","pending"]`.
URL을 한 줄씩 파일에 쓰고 `python3 news/collector/collect.py --urls <파일> --out /tmp/news-manual` 실행.
결과 문서에서 `title`이 비어 있으면 기존 문서의 제목을 유지한다.

## 4. 저장
- 먼저 `ArtifactData list` 로 `data/users/me/news/articles` 의 기존 문서 id와 version을 확인한다.
- 새 기사: `batch` 의 `set` (file_path로 JSON 파일 지정, if_version 없음). 50건씩.
- 이미 있는 기사: 다시 쓰지 않는다. 단 3번의 pending 기사는 `update` + `if_version` 으로 본문·상태·제목·발행 시각만 채운다.
  `bodyStatus`가 `pasted`인 문서(사용자가 붙여 넣은 본문)는 절대 덮어쓰지 않는다.
- 상태: `set` collection `data/users/me`, doc_id `status`, file_path `/tmp/news-out/status.json` (있으면 if_version).

## 5. 검색 확인 요청 처리
`ArtifactData query` collection `data/users/me/news/verify`, where `["status","==","pending"]`.
각 요청마다:
- `WebSearch`(필요하면 `WebFetch`)로 `query`를 실제 검색해 질문(`question`)에 답한다.
- 문서를 `update`(if_version) 한다:
  `status: "done"` 과 `result: {answer, sources:[{title,url}], limits, checkedAt}`.
  `answer`는 검색 결과로 확인된 내용만 짧게, `sources`는 실제로 연 페이지의 URL만.
- 확인하지 못했으면 `status: "failed"`, `result.limits`에 이유. 출처나 수치를 지어내지 않는다.

## 6. 보고
매체별 수집 건수, 본문 확보/확인 불가 건수, 처리한 검색 요청 수를 짧게 남기고 끝낸다.
