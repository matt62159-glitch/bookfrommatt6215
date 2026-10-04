#!/usr/bin/env python3
"""아침 뉴스 노트 수집기.

세 매체(동아일보, 한국일보, AI타임스)의 공식 RSS에서 기사 목록을 받고,
기사마다 원문 페이지에서 본문을 따로 확보한다. 결과는 JSON 파일로 남기고,
앱 저장소(ArtifactData)에 쓰는 일은 이 스크립트를 실행한 Claude 세션이 맡는다.

원칙
- 공식 RSS를 먼저 쓴다. 본문은 일반 브라우저처럼 공개 페이지를 한 번 요청할 뿐,
  로그인·유료벽·접근 제한을 우회하지 않는다. robots.txt가 막으면 본문을 받지 않는다.
- 본문을 확보하지 못하면 bodyStatus="unavailable"과 이유를 남긴다. 가짜 기사로 채우지 않는다.

표준 라이브러리만 쓴다.

사용 예
  python3 collect.py --hours 24 --per-outlet 5 --out out
  python3 collect.py --urls manual.txt --out out     # 직접 넣은 URL의 본문만 확보
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

KST = dt.timezone(dt.timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; MorningNewsNote/1.0; personal reader)"
TIMEOUT = 20
BODY_MAX = 20000
BODY_MIN = 200  # 이보다 짧으면 본문을 확보하지 못한 것으로 본다

OUTLETS = {
    "donga": {
        "name": "동아일보",
        "hosts": ["donga.com"],
        # (피드 URL, 피드가 뜻하는 분야). 분야 피드를 먼저 읽어 경제·기술을 우선한다.
        "feeds": [
            ("https://rss.donga.com/economy.xml", "경제"),
            ("https://rss.donga.com/science.xml", "기술"),
            ("https://rss.donga.com/total.xml", None),
        ],
        "discover": None,
        "body": [("section", "class", "news_view"), ("div", "class", "article_txt"),
                 ("div", "id", "article_txt")],
        "prioritize": True,
    },
    "hankook": {
        "name": "한국일보",
        "hosts": ["hankookilbo.com"],
        "feeds": [],
        # 한국일보는 RSS 안내 페이지에서 피드 주소를 찾는다.
        "discover": "https://www.hankookilbo.com/RSS",
        "body": [("div", "class", "col-main"), ("div", "class", "article-story"),
                 ("p", "class", "editor-p")],
        "prioritize": True,
    },
    "aitimes": {
        "name": "AI타임스",
        "hosts": ["aitimes.com"],
        "feeds": [("https://www.aitimes.com/rss/allArticle.xml", "기술")],
        "discover": None,
        "body": [("article", "id", "article-view-content-div"),
                 ("div", "id", "article-view-content-div")],
        "prioritize": False,
    },
}

ECON_WORDS = ["경제", "economy", "산업", "금융", "증권", "부동산", "기업", "재테크", "money", "finance", "business"]
TECH_WORDS = ["기술", "it", "과학", "science", "테크", "tech", "ai", "인공지능", "디지털", "모바일", "반도체", "게임"]


# ---------------------------------------------------------------- URL & ID

def normalize_url(url: str) -> str:
    """같은 기사를 같은 문자열로 만든다. 앱(index.html)의 normalizeUrl과 같은 규칙."""
    url = url.strip()
    p = urllib.parse.urlsplit(url)
    scheme = "https"
    host = p.hostname.lower() if p.hostname else ""
    if host.startswith("m."):
        host = "www." + host[2:]
    path = re.sub(r"/+$", "", p.path) or "/"
    keep = []
    for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=False):
        kl = k.lower()
        if kl.startswith("utm_") or kl in ("fbclid", "gclid", "ref", "from", "rss", "sns"):
            continue
        keep.append((k, v))
    keep.sort()
    query = urllib.parse.urlencode(keep)
    return f"{scheme}://{host}{path}" + (f"?{query}" if query else "")


def article_id(url: str) -> str:
    """정규화한 URL의 FNV-1a 64비트 해시. 앱의 articleId와 같은 값."""
    h = 0xCBF29CE484222325
    for b in normalize_url(url).encode("utf-8"):
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return "a" + format(h, "016x")


def outlet_for(url: str) -> str | None:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    for key, o in OUTLETS.items():
        if any(host == h or host.endswith("." + h) for h in o["hosts"]):
            return key
    return None


# ---------------------------------------------------------------- 분야

def classify(*hints: str | None) -> str:
    text = " ".join(h for h in hints if h).lower()
    if any(w in text for w in ECON_WORDS):
        return "경제"
    if any(re.search(r"(^|[^a-z])" + re.escape(w) + r"([^a-z]|$)", text) if w.isascii() else w in text
           for w in TECH_WORDS):
        return "기술"
    return "기타"


# ---------------------------------------------------------------- HTTP

class FetchError(Exception):
    pass


def fetch(url: str) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read(), r.headers.get_content_charset() or ""
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchError(f"접속 실패: {getattr(e, 'reason', e)}") from e


def decode(raw: bytes, charset: str) -> str:
    for enc in [charset, "utf-8", "cp949"]:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def robots_allows(url: str) -> bool:
    p = urllib.parse.urlsplit(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            raw, cs = fetch(base + "/robots.txt")
            rp.parse(decode(raw, cs).splitlines())
            _robots[base] = rp
        except FetchError:
            _robots[base] = None  # robots.txt를 못 읽으면 막지 않는다(표준 관례)
    rp = _robots[base]
    return True if rp is None else rp.can_fetch(UA, url)


# ---------------------------------------------------------------- RSS

def parse_date(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        d = None
    if d is None:
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?", s)
            if not m:
                return None
            d = dt.datetime(*[int(x or 0) for x in m.groups()], tzinfo=KST)
    if d.tzinfo is None:
        d = d.replace(tzinfo=KST)  # 시간대 없는 국내 매체 시각은 한국시간으로 본다
    return d.astimezone(KST)


def _text(el, *names) -> str:
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return html.unescape(found.text.strip())
    return ""


def parse_feed(xml_bytes: bytes, feed_section: str | None) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall(".//item") or root.findall(f".//{atom}entry")
    out = []
    for it in items:
        link = _text(it, "link")
        if not link:
            le = it.find(f"{atom}link")
            link = le.get("href", "") if le is not None else ""
        title = re.sub(r"\s+", " ", _text(it, "title", f"{atom}title"))
        if not link or not title:
            continue
        cats = [html.unescape(c.text.strip()) for c in it.findall("category") if c.text]
        pub = parse_date(_text(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date",
                               f"{atom}published", f"{atom}updated"))
        out.append({
            "url": link.strip(),
            "title": title,
            "section": feed_section or (cats[0] if cats else ""),
            "rawCategories": cats,
            "publishedAt": pub.isoformat() if pub else None,
        })
    return out


class _LinkFinder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if re.search(r"rss", href, re.I):
                self.links.append(href)


def discover_feeds(page_url: str) -> list[tuple[str, str | None]]:
    raw, cs = fetch(page_url)
    f = _LinkFinder()
    f.feed(decode(raw, cs))
    seen, feeds = set(), []
    for href in f.links:
        u = urllib.parse.urljoin(page_url, href)
        if u in seen or u.rstrip("/") == page_url.rstrip("/"):
            continue
        seen.add(u)
        feeds.append((u, None))
    # 경제·기술 피드를 먼저 읽게 정렬
    feeds.sort(key=lambda f: 0 if classify(f[0]) != "기타" else 1)
    return feeds[:12]


# ---------------------------------------------------------------- 본문

_SKIP = {"script", "style", "noscript", "figure", "figcaption", "button", "aside", "iframe", "svg", "form", "table"}
_VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "area", "base", "col", "embed", "param", "track"}
_BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "section", "article"}


class _BodyExtractor(HTMLParser):
    """지정한 태그·속성 안의 글자만 모은다. 여러 개가 맞으면 모두 이어 붙인다."""

    def __init__(self, tag: str, attr: str, value: str):
        super().__init__(convert_charrefs=True)
        self.tag, self.attr, self.value = tag, attr, value
        self.depth = 0          # 대상 요소 안의 깊이 (0이면 밖)
        self.skip = 0
        self.parts: list[str] = []
        self.meta: dict[str, str] = {}

    def _match(self, tag, attrs) -> bool:
        if tag != self.tag:
            return False
        v = dict(attrs).get(self.attr) or ""
        return self.value in v.split() if self.attr == "class" else v == self.value

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            a = dict(attrs)
            k = a.get("property") or a.get("name")
            if k and a.get("content"):
                self.meta.setdefault(k, a["content"])
            return
        if tag in _VOID:
            if self.depth and tag == "br":
                self.parts.append("\n")
            return
        if self.depth:
            self.depth += 1
            if tag in _SKIP or self.skip:
                self.skip += 1
        elif self._match(tag, attrs):
            self.depth = 1
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _VOID or not self.depth:
            return
        if self.skip:
            self.skip -= 1
        self.depth -= 1
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.depth and not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        t = "".join(self.parts)
        lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in t.split("\n")]
        return "\n".join(ln for ln in lines if ln)


def extract_body(page: str, selectors: list[tuple[str, str, str]]) -> tuple[str, dict]:
    meta: dict[str, str] = {}
    for sel in selectors:
        ex = _BodyExtractor(*sel)
        ex.feed(page)
        meta = meta or ex.meta
        body = ex.text()
        if len(body) >= BODY_MIN:
            return body[:BODY_MAX], ex.meta
    # 매체별 위치를 못 찾으면 <article> 안의 문단을 시도
    ex = _BodyExtractor("article", "class", "")
    ex._match = lambda tag, attrs: tag == "article"  # type: ignore[assignment]
    ex.feed(page)
    body = ex.text()
    return (body[:BODY_MAX] if len(body) >= BODY_MIN else ""), (meta or ex.meta)


PAYWALL_HINTS = ["유료 회원", "구독자 전용", "로그인 후 이용", "프리미엄 기사", "구독 후 이용"]


def fetch_body(url: str, outlet: str | None) -> dict:
    """본문 확보 결과: {bodyStatus, body, bodyNote, meta}."""
    if not robots_allows(url):
        return {"bodyStatus": "unavailable", "body": "", "bodyNote": "사이트 robots.txt가 자동 수집을 허용하지 않음"}
    try:
        raw, cs = fetch(url)
    except FetchError as e:
        return {"bodyStatus": "unavailable", "body": "", "bodyNote": f"원문 페이지를 열 수 없음 ({e})"}
    page = decode(raw, cs)
    sels = OUTLETS[outlet]["body"] if outlet else []
    body, meta = extract_body(page, sels)
    if not body:
        hint = next((h for h in PAYWALL_HINTS if h in page), None)
        note = f"유료·회원 전용으로 보임 ('{hint}')" if hint else "페이지에서 본문 위치를 찾지 못함"
        return {"bodyStatus": "unavailable", "body": "", "bodyNote": note, "meta": meta}
    return {"bodyStatus": "ok", "body": body, "bodyNote": f"원문 페이지에서 확보 ({len(body)}자)", "meta": meta}


# ---------------------------------------------------------------- 수집

def collect_outlet(key: str, since: dt.datetime, per_outlet: int, now: dt.datetime) -> tuple[list[dict], dict]:
    o = OUTLETS[key]
    status = {"name": o["name"], "ok": False, "count": 0, "error": "", "feeds": [], "checkedAt": now.isoformat()}
    feeds = list(o["feeds"])
    if o["discover"]:
        try:
            feeds = discover_feeds(o["discover"]) + feeds
        except FetchError as e:
            status["error"] = f"RSS 안내 페이지 접속 실패 ({e})"
    seen: dict[str, dict] = {}
    for url, section in feeds:
        try:
            raw, _ = fetch(url)
            items = parse_feed(raw, section)
            status["feeds"].append({"url": url, "ok": True, "items": len(items)})
        except (FetchError, ET.ParseError) as e:
            status["feeds"].append({"url": url, "ok": False, "error": str(e)})
            continue
        for it in items:
            aid = article_id(it["url"])
            if aid in seen:  # 같은 URL은 한 번만. 분야 피드에서 먼저 본 분야를 유지
                continue
            it["category"] = classify(it["section"], " ".join(it["rawCategories"]), it["url"])
            if key == "aitimes" and it["category"] == "기타":
                it["category"] = "기술"
            seen[aid] = it
    ok_feeds = [f for f in status["feeds"] if f["ok"]]
    if not ok_feeds:
        status["error"] = status["error"] or "읽을 수 있는 RSS가 없음"
        return [], status

    recent = [it for it in seen.values() if it["publishedAt"] and dt.datetime.fromisoformat(it["publishedAt"]) >= since]
    rank = (lambda it: (0 if it["category"] in ("경제", "기술") else 1)) if o["prioritize"] else (lambda it: 0)
    recent.sort(key=lambda it: it["publishedAt"], reverse=True)
    recent.sort(key=rank)  # 안정 정렬: 분야 우선, 같은 순위 안에서는 최신순
    picked = recent[:per_outlet]

    out = []
    for it in picked:
        b = fetch_body(it["url"], key)
        out.append(make_doc(it, key, b, now, source="rss"))
    status.update(ok=True, count=len(out), error="" if out else "기간 안에 발행된 기사가 없음")
    return out, status


def make_doc(it: dict, outlet: str | None, b: dict, now: dt.datetime, source: str) -> dict:
    url = normalize_url(it["url"])
    return {
        "id": article_id(url),
        "url": url,
        "outlet": outlet or "other",
        "outletName": OUTLETS[outlet]["name"] if outlet else (urllib.parse.urlsplit(url).hostname or "기타"),
        "title": it.get("title", ""),
        "section": it.get("section", ""),
        "category": it.get("category", "기타"),
        "publishedAt": it.get("publishedAt"),
        "collectedAt": now.isoformat(),
        "source": source,
        "bodyStatus": b["bodyStatus"],
        "body": b["body"],
        "bodyNote": b["bodyNote"],
        "demo": False,
    }


def collect_urls(urls: list[str], now: dt.datetime) -> list[dict]:
    """사용자가 직접 넣은 URL: 본문과 메타데이터(제목·발행 시각)를 확보한다."""
    out = []
    for u in urls:
        key = outlet_for(u)
        b = fetch_body(u, key)
        meta = b.get("meta") or {}
        pub = parse_date(meta.get("article:published_time") or meta.get("og:article:published_time"))
        it = {
            "url": u,
            "title": html.unescape(meta.get("og:title", "")).strip(),
            "section": meta.get("article:section", ""),
            "publishedAt": pub.isoformat() if pub else None,
        }
        it["category"] = classify(it["section"], u)
        out.append(make_doc(it, key, b, now, source="manual"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=float, default=24, help="최근 몇 시간 안에 발행된 기사 (기본 24)")
    ap.add_argument("--per-outlet", type=int, default=5, help="매체별 최대 기사 수 (기본 5)")
    ap.add_argument("--outlets", default="donga,hankook,aitimes")
    ap.add_argument("--urls", help="직접 넣은 기사 URL 목록 파일(한 줄에 하나). 주면 RSS 수집은 건너뛴다")
    ap.add_argument("--out", default="out")
    a = ap.parse_args(argv)

    now = dt.datetime.now(KST).replace(microsecond=0)
    os.makedirs(os.path.join(a.out, "articles"), exist_ok=True)
    docs: list[dict] = []
    status: dict = {"lastRunAt": now.isoformat(), "hours": a.hours, "perOutlet": a.per_outlet, "outlets": {}}

    if a.urls:
        with open(a.urls, encoding="utf-8") as f:
            urls = [ln.strip() for ln in f if ln.strip().startswith("http")]
        docs = collect_urls(urls, now)
        status = None
    else:
        since = now - dt.timedelta(hours=a.hours)
        for key in a.outlets.split(","):
            items, st = collect_outlet(key.strip(), since, a.per_outlet, now)
            docs += items
            status["outlets"][key] = st

    for d in docs:
        with open(os.path.join(a.out, "articles", d["id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    if status:
        with open(os.path.join(a.out, "status.json"), "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=1)

    for d in docs:
        print(f"[{d['outletName']}] {d['bodyStatus']:<11} {d['category']} {d['publishedAt']} {d['title'][:50]}")
    if status:
        for key, st in status["outlets"].items():
            print(f"상태 {st['name']}: {'성공' if st['ok'] else '실패'} {st['count']}건 {st['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
