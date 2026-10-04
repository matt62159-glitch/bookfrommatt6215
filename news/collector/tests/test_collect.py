import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect as c  # noqa: E402

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title><![CDATA[금리 동결 &amp; 환율]]></title><link>https://www.donga.com/news/Economy/article/all/20261004/1/1?utm_source=rss</link>
<pubDate>Sun, 04 Oct 2026 07:10:00 +0900</pubDate><category>경제</category></item>
<item><title>야구 결과</title><link>https://www.donga.com/news/Sports/article/all/20261003/2/1</link>
<pubDate>Sat, 03 Oct 2026 09:00:00 +0900</pubDate></item>
</channel></rss>"""

PAGE = """<html><head><meta property="og:title" content="제목 &amp; 부제">
<meta property="article:published_time" content="2026-10-04T06:00:00+09:00"></head><body>
<section class="news_view">첫 문단입니다.<br>둘째 줄
<figure><img src="x.jpg"><figcaption>사진 설명은 빠져야 함</figcaption></figure>
<script>var x = 1;</script><p>""" + "본문 내용 " * 40 + """</p></section><div>광고</div></body></html>"""


class T(unittest.TestCase):
    def test_normalize_and_id(self):
        a = c.normalize_url("http://m.aitimes.com/news/articleView.html?idxno=123&utm_source=x#top")
        self.assertEqual(a, "https://www.aitimes.com/news/articleView.html?idxno=123")
        self.assertEqual(c.article_id(a), c.article_id("https://www.aitimes.com/news/articleView.html/?idxno=123"))
        self.assertNotEqual(c.article_id(a), c.article_id(a.replace("123", "124")))

    def test_feed(self):
        items = c.parse_feed(RSS.encode(), None)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "금리 동결 & 환율")
        self.assertEqual(items[0]["publishedAt"], "2026-10-04T07:10:00+09:00")
        self.assertEqual(c.classify(items[0]["section"]), "경제")
        self.assertEqual(c.classify("", items[1]["url"]), "기타")
        self.assertEqual(c.classify("기술"), "기술")
        self.assertEqual(c.classify("IT/과학"), "기술")

    def test_body(self):
        body, meta = c.extract_body(PAGE, c.OUTLETS["donga"]["body"])
        self.assertTrue(body.startswith("첫 문단입니다.\n둘째 줄"))
        self.assertNotIn("사진 설명", body)
        self.assertNotIn("var x", body)
        self.assertNotIn("광고", body)
        self.assertEqual(meta["og:title"], "제목 & 부제")

    def test_short_body_is_unavailable(self):
        body, _ = c.extract_body("<section class='news_view'>짧음</section>", c.OUTLETS["donga"]["body"])
        self.assertEqual(body, "")

    def test_repeated_paragraph_selector(self):
        page = "".join(f"<p class='editor-p'>{'문장 ' * 30}{i}</p><p>무시</p>" for i in range(3))
        body, _ = c.extract_body(page, [("p", "class", "editor-p")])
        self.assertEqual(body.count("문장"), 90)
        self.assertNotIn("무시", body)

    def test_naive_date_is_kst(self):
        self.assertEqual(c.parse_date("2026-10-04 08:30:00").isoformat(), "2026-10-04T08:30:00+09:00")


if __name__ == "__main__":
    unittest.main()
