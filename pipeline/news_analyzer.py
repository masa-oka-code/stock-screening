"""
news_analyzer.py
ニュース解析モジュール
・VADER感情分析（英語）
・キーワードマッチ（日本語対応）
・セクター分類
・銘柄別ニューススコア算出
"""

import logging
import re
from collections import defaultdict
from typing import Optional

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from pipeline.news_fetcher import SECTOR_KEYWORDS, CRISIS_KEYWORDS

logger = logging.getLogger(__name__)


def translate_to_ja(text: str) -> str:
    """英語テキストを日本語に翻訳（Google翻訳・無料・APIキー不要）"""
    if not text:
        return text
    import re, requests
    # 日本語が20%以上あればスキップ
    jp_ratio = len(re.findall(r'[　-鿿]', text)) / max(len(text), 1)
    if jp_ratio > 0.2:
        return text
    try:
        params = {"client": "gtx", "sl": "auto", "tl": "ja", "dt": "t", "q": text[:200]}
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params=params, timeout=5
        )
        data = r.json()
        return "".join([x[0] for x in data[0] if x[0]])
    except Exception:
        return text  # 翻訳失敗時は原文


def translate_articles(articles: list) -> list:
    """記事タイトルを日本語に翻訳してtitle_jaフィールドを追加"""
    import time
    translated = []
    for i, a in enumerate(articles):
        a = dict(a)
        title = a.get("title", "")
        a["title_ja"] = translate_to_ja(title)
        translated.append(a)
        # レート制限対策：10件ごとに少し待機
        if i > 0 and i % 10 == 0:
            time.sleep(0.5)
    logger.info(f"翻訳完了: {len(translated)}件")
    return translated

# VADER初期化（英語感情分析）
_vader = SentimentIntensityAnalyzer()

# 銘柄名→ティッカーのマッピング（ニュース中の企業名を検出）
TICKER_NAME_MAP = {
    # 米国株
    "apple":      "AAPL",  "iphone": "AAPL",
    "microsoft":  "MSFT",  "azure": "MSFT",
    "google":     "GOOGL", "alphabet": "GOOGL", "youtube": "GOOGL",
    "amazon":     "AMZN",  "aws": "AMZN",
    "nvidia":     "NVDA",  "cuda": "NVDA",
    "meta":       "META",  "facebook": "META", "instagram": "META",
    "tesla":      "TSLA",
    "jpmorgan":   "JPM",   "jp morgan": "JPM",
    "johnson":    "JNJ",
    "coca-cola":  "KO",    "coca cola": "KO",
    "berkshire":  "BRK-B",
    "visa":       "V",
    "walmart":    "WMT",
    "netflix":    "NFLX",
    "disney":     "DIS",
    # 日本株
    "トヨタ":     "7203.T", "toyota": "7203.T",
    "ソニー":     "6758.T", "sony": "6758.T",
    "ソフトバンク": "9984.T", "softbank": "9984.T",
    "任天堂":     "7974.T", "nintendo": "7974.T",
    "武田":       "4502.T", "takeda": "4502.T",
    "三菱UFJ":   "8306.T", "mufg": "8306.T",
    "キーエンス": "6861.T", "keyence": "6861.T",
    "信越化学":   "4063.T",
    "ファーストリテイリング": "9983.T", "ユニクロ": "9983.T", "uniqlo": "9983.T",
    "デンソー":   "6902.T", "denso": "6902.T",
}


def analyze_sentiment(text: str) -> float:
    """
    テキストの感情スコアを返す（-1.0〜+1.0）
    英語はVADER、日本語はキーワードマッチで判定
    """
    if not text:
        return 0.0

    # 英語テキスト（VADERで解析）
    english_ratio = len(re.findall(r'[a-zA-Z]', text)) / max(len(text), 1)
    if english_ratio > 0.3:
        scores = _vader.polarity_scores(text)
        return float(scores["compound"])   # -1〜+1

    # 日本語テキスト（キーワードマッチ）
    return _japanese_sentiment(text)


def _japanese_sentiment(text: str) -> float:
    """日本語テキストのキーワードベース感情判定"""
    positive_words = [
        "上昇", "増益", "黒字", "好調", "拡大", "成長", "上方修正",
        "最高益", "増配", "自社株買い", "好業績", "需要増",
    ]
    negative_words = [
        "下落", "減益", "赤字", "不振", "縮小", "下方修正",
        "最安値", "減配", "リストラ", "業績悪化", "需要減",
        "制裁", "戦争", "破綻", "暴落", "緊急",
    ]

    pos = sum(1 for w in positive_words if w in text)
    neg = sum(1 for w in negative_words if w in text)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


def classify_sectors(text: str) -> list:
    """テキストからセクターリストを抽出"""
    text_lower = text.lower()
    matched = []
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(kw.lower() in text_lower for kw in keywords):
            matched.append(sector)
    return matched if matched else ["General"]


def detect_crisis_keywords(text: str) -> int:
    """高危険キーワードのヒット数を返す（マクロフィルターに使用）"""
    text_lower = text.lower()
    return sum(1 for kw in CRISIS_KEYWORDS if kw.lower() in text_lower)


def find_mentioned_tickers(text: str) -> list:
    """テキスト中に言及されている銘柄ティッカーを抽出"""
    text_lower = text.lower()
    found = set()
    for keyword, ticker in TICKER_NAME_MAP.items():
        if keyword.lower() in text_lower:
            found.add(ticker)
    return list(found)


def analyze_articles(articles: list) -> dict:
    """
    全ニュース記事を解析してまとめた結果を返す

    戻り値:
      sector_scores   : セクター別スコア {sector: float}
      ticker_scores   : 銘柄別スコア {ticker: float}
      crisis_count    : 危機キーワード総数
      top_themes      : 上位テーマリスト
      article_details : 記事ごとの解析結果
    """
    sector_sentiments  = defaultdict(list)
    ticker_sentiments  = defaultdict(list)
    crisis_total       = 0
    article_details    = []

    for article in articles:
        text      = article.get("text", "")
        sentiment = analyze_sentiment(text)
        sectors   = classify_sectors(text)
        tickers   = find_mentioned_tickers(text)
        crisis    = detect_crisis_keywords(text)

        crisis_total += crisis

        for sector in sectors:
            sector_sentiments[sector].append(sentiment)
        for ticker in tickers:
            ticker_sentiments[ticker].append(sentiment)

        article_details.append({
            "title":     article.get("title", ""),
            "source":    article.get("source", ""),
            "sentiment": round(sentiment, 3),
            "sectors":   sectors,
            "tickers":   tickers,
            "crisis":    crisis,
            "published": article.get("published", ""),
        })

    # セクター別平均スコア（-1〜+1 → 0〜30点に変換）
    sector_scores = {}
    for sector, scores in sector_sentiments.items():
        avg = sum(scores) / len(scores)
        # -1〜+1 → 0〜30点（中立=15点）
        sector_scores[sector] = round((avg + 1) / 2 * 30, 1)

    # 銘柄別平均スコア（0〜30点）
    ticker_scores = {}
    for ticker, scores in ticker_sentiments.items():
        avg = sum(scores) / len(scores)
        ticker_scores[ticker] = round((avg + 1) / 2 * 30, 1)

    # 上位テーマ（ポジティブなセクター上位3）
    top_positive = sorted(
        [(s, v) for s, v in sector_scores.items() if v > 15],
        key=lambda x: -x[1]
    )[:3]
    top_negative = sorted(
        [(s, v) for s, v in sector_scores.items() if v < 15],
        key=lambda x: x[1]
    )[:3]

    # 急性危機KWの検出内容を記録
    from pipeline.news_fetcher import ACUTE_CRISIS_KEYWORDS
    import re as _re
    acute_found = []
    for a in articles:
        text = a.get("text", "").lower()
        for kw in ACUTE_CRISIS_KEYWORDS:
            k = kw.lower()
            has_jp = bool(_re.search("[^\x00-\x7F]", k))
            if has_jp:
                matched = k in text
            else:
                matched = bool(_re.search(r"\b" + _re.escape(k) + r"\b", text))
            if matched and kw not in acute_found:
                acute_found.append(kw)

    # 記事タイトルを日本語に翻訳
    logger.info("記事タイトルを日本語翻訳中...")
    article_details = translate_articles(article_details)

    logger.info(f"ニュース解析完了: {len(articles)}件  危機KW:{crisis_total}")
    logger.info(f"  ポジセクター: {[s for s,_ in top_positive]}")
    logger.info(f"  ネガセクター: {[s for s,_ in top_negative]}")

    return {
        "sector_scores":   sector_scores,
        "ticker_scores":   ticker_scores,
        "crisis_count":    crisis_total,
        "acute_count":     len(acute_found),
        "chronic_count":   crisis_total - len(acute_found),
        "acute_keywords":  acute_found,
        "top_positive":    top_positive,
        "top_negative":    top_negative,
        "article_count":   len(articles),
        "article_details": article_details[:80],
    }


def get_news_score_for_ticker(ticker: str, news_analysis: dict,
                               sector: str = "") -> int:
    """
    銘柄のニューススコアを取得（0〜30点）
    1. 銘柄直接言及スコアがあればそれを使用
    2. なければセクタースコアを使用
    3. どちらもなければ中立15点
    """
    ticker_scores = news_analysis.get("ticker_scores", {})
    sector_scores = news_analysis.get("sector_scores", {})

    if ticker in ticker_scores:
        return int(ticker_scores[ticker])

    # セクターマッチング（部分一致）
    for s_key, s_score in sector_scores.items():
        if s_key.lower() in (sector or "").lower() or (sector or "").lower() in s_key.lower():
            return int(s_score)

    return 15   # 中立


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pipeline.news_fetcher import fetch_rss_news

    print("RSSニュース取得中...")
    articles = fetch_rss_news(hours=48)
    print(f"取得: {len(articles)}件\n")

    if articles:
        result = analyze_articles(articles)
        print("【セクター別スコア】（15=中立, 30=最大ポジ, 0=最大ネガ）")
        for sector, score in sorted(result["sector_scores"].items(), key=lambda x: -x[1]):
            bar   = "█" * int(score / 2)
            label = "↑ポジ" if score > 17 else ("↓ネガ" if score < 13 else "→中立")
            print(f"  {sector:25s} {score:5.1f} {bar} {label}")

        print(f"\n【危機キーワード数】: {result['crisis_count']}")
        print(f"\n【ポジティブテーマ】: {[s for s,_ in result['top_positive']]}")
        print(f"【ネガティブテーマ】: {[s for s,_ in result['top_negative']]}")

        if result["ticker_scores"]:
            print("\n【言及銘柄スコア】")
            for t, s in sorted(result["ticker_scores"].items(), key=lambda x: -x[1]):
                print(f"  {t:10s}: {s:.1f}")
    else:
        print("記事が取得できませんでした（RSSアクセス制限の可能性）")