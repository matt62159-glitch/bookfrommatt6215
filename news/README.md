# 아침 뉴스 노트

동아일보·한국일보·AI타임스 기사를 본문 근거로 요약·해설하고, 기사별로 질문하고, 기록을 남기는 개인용 웹앱.

- 앱(Artifact): https://claude.ai/artifact/8CXzcr6VTcg7Cfs6nD6G3E — `index.html`
- 수집기: `collector/collect.py` (표준 라이브러리만 사용, 테스트 `python3 -m unittest discover -s collector/tests`)
- 수집 루틴 지시서: `ROUTINE.md`

## 구조

```
[수집 루틴: Claude Code 클라우드 세션]                     [앱: Artifact 페이지]
  collect.py → RSS 목록 → 원문 본문 확보                     목록·필터·해설·질문·기록 화면
  WebSearch → 검색 확인 요청 처리           ──ArtifactData──▶  db (data/users/<내 ID>/…)
        ▲                                                      │
        └──────── fire_trigger (Claude Code Remote 커넥터) ◀──── '새 기사 불러오기' 버튼
                                                               sample → Claude 요약·해설·질문 답변
```

## 현재 환경에서 확인한 결과 (2026-10-04)

| 항목 | 결과 | 비고 |
|---|---|---|
| 매체별 기사 목록 수집 | 이 클라우드 환경에서 막힘 | rss.donga.com, www.hankookilbo.com, www.aitimes.com 모두 네트워크 정책이 403으로 차단. 허용 도메인에 추가하면 수집기가 동작할 수 있다 (실제 사이트 구조로는 아직 검증 못 함). |
| 기사 본문 확보 | 위와 같음 | 수집기는 공개 페이지만 요청하고 robots.txt를 지킨다. 유료·회원 전용은 ‘본문 확인 불가’. |
| AI 요약·해설 | 앱 안에서 가능 | Artifact `sample` 기능. 본인 Claude 이용량을 쓴다. 별도 API 키 불필요. |
| 추가 웹 검색 | 앱 안에서는 불가 | 페이지는 외부 접속이 막혀 있다. ‘검색 확인 요청’을 저장해 두면 수집 루틴(WebSearch)이 처리한다. |
| 개인 기록 저장 | 앱 안에서 가능 | `db` 의 `data/users/<내 ID>/` — 본인만 읽고 쓴다. 기기·브라우저를 바꿔도 남는다. |

## 수집을 켜려면

1. 클라우드 환경 설정 → Network access → Custom, 허용 도메인에 추가:
   `rss.donga.com`, `www.donga.com`, `www.hankookilbo.com`, `www.aitimes.com`
2. 수집 루틴(Routine) 만들기: 이 저장소, 프롬프트 "news/ROUTINE.md를 읽고 그대로 수행", 매번 새 세션.
   예약 시각 없이 만들면 버튼으로만 실행된다. 받은 `trig_…` ID를 앱의 ‘설정·안내’에 넣는다.
3. 매일 아침 자동 수집(후속 단계): 같은 루틴에 cron 예약을 붙인다. 예: `CRON_TZ=Asia/Seoul 50 6 * * *` (매일 06:50).
   루틴은 claude.ai 서버에서 돌기 때문에 브라우저나 컴퓨터를 꺼도 실행된다.

비용: 별도 외부 API나 비밀키는 필요 없다. 루틴 실행, 요약·해설, 질문 답변은 모두 본인 Claude 이용량을 쓴다.

## 저장 구조 (`data/users/<내 ID>`)

- `settings` 기간·기사 수·루틴 ID · `status` 마지막 수집 시각과 매체별 성공·실패
- `news/articles/<id>` 기사(id = 정규화한 URL의 FNV-1a 해시, 같은 URL 중복 제거) — 본문 포함, 화면에는 본문 전문을 싣지 않음
- `news/analyses/<id>` 요약·해설 · `news/chats/<id>` 기사별 질문 대화 · `news/notes/<id>` 읽음·북마크·메모·저장 항목 · `news/verify/<id>` 검색 확인 요청과 결과
