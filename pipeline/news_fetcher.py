"""
news_fetcher.py
ニュース取得モジュール
"""

import feedparser
import requests
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

RSS_FEEDS = {
    "nhk_economy":    "https://www3.nhk.or.jp/rss/news/cat6.xml",
    "yahoo_jp_stock": "https://news.yahoo.co.jp/rss/topics/business.xml",
    "reuters_biz":    "https://feeds.reuters.com/reuters/businessNews",
    "yahoo_finance":  "https://finance.yahoo.com/rss/topstories",
    "marketwatch":    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "cnbc_world":     "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "investing_com":  "https://www.investing.com/rss/news.rss",
}

SECTOR_KEYWORDS = {
    "Technology": [
        "AI", "chip", "semiconductor", "cloud", "software", "tech",
        "nvidia", "apple", "microsoft", "google", "amazon", "meta",
        "半導体", "クラウド", "ソフトウェア", "テック",
    ],
    "Healthcare": [
        "drug", "pharma", "FDA", "clinical", "biotech", "vaccine", "medical",
        "製薬", "医薬", "臨床", "バイオ", "ワクチン",
    ],
    "Financials": [
        "bank", "interest rate", "Fed", "BOJ", "financial", "credit", "loan",
        "銀行", "金利", "日銀", "FRB", "融資",
    ],
    "Energy": [
        "oil", "gas", "crude", "OPEC", "energy", "renewable",
        "石油", "原油", "ガス", "エネルギー",
    ],
    "Consumer": [
        "retail", "consumer", "spending", "inflation", "CPI",
        "小売", "消費", "インフレ", "物価",
    ],
    "Industrials": [
        "manufacturing", "factory", "supply chain", "auto",
        "製造", "工場", "サプライチェーン", "自動車",
    ],
    "Macro": [
        "recession", "GDP", "economy", "trade war", "tariff", "sanctions",
        "geopolitical", "inflation", "deflation",
        "景気後退", "GDP", "経済", "関税", "制裁", "地政学",
    ],
}

# ─────────────────────────────────────────
# 危機キーワード：2種類に分類
# ─────────────────────────────────────────

# 急性ショック：1件でもペナルティ（新規・突発的リスク）
ACUTE_CRISIS_KEYWORDS = [
    "bank run", "bank failure", "emergency rate",
    # "nuclear" 単体は原発・エネルギー政策でも反応するため攻撃的文脈に限定
    "nuclear weapon", "nuclear strike", "nuclear attack", "nuclear war",
    "nuclear missile", "nuclear threat",
    "debt ceiling", "sovereign default",
    "market crash", "circuit breaker",
    "取り付け騒ぎ", "金融危機", "緊急利上げ", "デフォルト",
    "核攻撃", "核ミサイル", "核戦争",  # 「核」単体は除外、攻撃文脈のみ
]

# 慢性リスク：「急増」したときだけペナルティ（織り込み済みリスク）
CHRONIC_CRISIS_KEYWORDS = [
    "war", "conflict", "invasion", "sanctions", "tariff",
    "戦争", "紛争", "侵攻", "制裁", "関税",
]

# 後方互換用（news_analyzerから参照）
CRISIS_KEYWORDS = ACUTE_CRISIS_KEYWORDS + CHRONIC_CRISIS_KEYWORDS


def fetch_rss_news(hours: int = 24) -> list:
    cutoff   = datetime.now(timezone.utc) - timedelta(hours=hours)
    articles = []
    for name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:20]:
                published = _parse_date(entry)
                if published and published < cutoff:
                    continue
                title   = getattr(entry, "title",   "") or ""
                summary = getattr(entry, "summary", "") or ""
                link    = getattr(entry, "link",    "") or ""
                articles.append({
                    "source":    name,
                    "title":     title,
                    "summary":   summary[:300],
                    "link":      link,
                    "published": published.isoformat() if published else "",
                    "text":      f"{title} {summary}",
                })
        except Exception as e:
            logger.debug(f"RSS取得失敗 {name}: {e}")
    logger.info(f"RSS取得完了: {len(articles)}件（過去{hours}時間）")
    return articles


def fetch_newsapi(api_key: str, query: str = "stock market economy", hours: int = 24) -> list:
    if not api_key:
        return []
    url = "https://newsapi.org/v2/everything"
    params = {
        "q":        query,
        "sortBy":   "publishedAt",
        "language": "en",
        "pageSize": 50,
        "apiKey":   api_key,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data     = resp.json()
        articles = []
        for a in data.get("articles", []):
            title   = a.get("title",       "") or ""
            summary = a.get("description", "") or ""
            articles.append({
                "source":    a.get("source", {}).get("name", "NewsAPI"),
                "title":     title,
                "summary":   summary[:300],
                "link":      a.get("url", ""),
                "published": a.get("publishedAt", ""),
                "text":      f"{title} {summary}",
            })
        logger.info(f"NewsAPI取得完了: {len(articles)}件")
        return articles
    except Exception as e:
        logger.warning(f"NewsAPI取得失敗: {e}")
        return []


def _parse_date(entry) -> Optional[datetime]:
    import time as _time
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime.fromtimestamp(_time.mktime(t), tz=timezone.utc)
            except Exception:
                pass
    return None


def fetch_all_news(config: dict) -> list:
    articles = fetch_rss_news(hours=24)
    api_key  = config.get("newsapi_key", "")
    if api_key:
        articles += fetch_newsapi(api_key, hours=24)
    seen   = set()
    unique = []
    for a in articles:
        key = a["title"][:50]
        if key not in seen:
            seen.add(key)
            unique.append(a)
    logger.info(f"ニュース合計（重複除去後）: {len(unique)}件")
    return unique


def calc_crisis_score(articles: list, prev_chronic_count: int = None) -> dict:
    """
    急性・慢性を分けた危機スコアを算出

    acute_count  : 急性ショックKWのヒット数（1件でも危険）
    chronic_count: 慢性リスクKWのヒット数（急増しない限り無視）
    chronic_surge: 慢性KWが通常の2倍以上に急増したか
    crisis_score : マクロスコアへのペナルティ値（負の数）
    """
    def word_match(text: str, kw: str) -> bool:
        t = text.lower()
        k = kw.lower()
        # 日本語は部分一致、英語は単語境界
        if re.search(r'[^\x00-\x7F]', k):
            return k in t
        return bool(re.search(r'\b' + re.escape(k) + r'\b', t))

    acute_count   = 0
    chronic_count = 0

    for a in articles:
        text = a.get("text", "")
        for kw in ACUTE_CRISIS_KEYWORDS:
            if word_match(text, kw):
                acute_count += 1
        for kw in CHRONIC_CRISIS_KEYWORDS:
            if word_match(text, kw):
                chronic_count += 1

    # 慢性KWの急増判定
    # 前回件数がなければ20件を「通常」の基準とする
    baseline        = prev_chronic_count if prev_chronic_count is not None else 20
    chronic_surge   = chronic_count > baseline * 2.0 and chronic_count > 25
    crisis_score    = 0

    # 急性ショック：1件ごとに-8点
    if acute_count > 0:
        crisis_score -= acute_count * 8

    # 慢性リスク急増：通常時はペナルティなし、急増時のみ-15点
    if chronic_surge:
        crisis_score -= 15

    return {
        "acute_count":    acute_count,
        "chronic_count":  chronic_count,
        "chronic_surge":  chronic_surge,
        "crisis_score":   crisis_score,
        "total_count":    acute_count + chronic_count,
    }