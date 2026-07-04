"""
news_analyzer.py（短期ニュース分類ロジック強化版）
"""

import logging
import re
from collections import defaultdict
from typing import Optional

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from pipeline.news_fetcher import SECTOR_KEYWORDS, CRISIS_KEYWORDS
from pipeline.market_data import get_stock_data

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 短期ニュース分類ロジック（3軸判定）
# ─────────────────────────────────────────

SHORT_CATEGORIES = [
    "事故","炎上","不具合","停止","障害","リコール","一時的",
    "供給不足","システム障害","短期","速報","急落","急騰"
]

MID_CATEGORIES = [
    "下方修正","減益","コスト増","競争激化","市場縮小",
    "業績悪化","構造変化"
]

LONG_CATEGORIES = [
    "新規事業","戦略","規制","補助金","技術革新","M&A",
    "大型投資","長期","地政学","人口","インフラ"
]

SHORT_PATTERNS = [
    r"一時的", r"停止", r"復旧", r"障害", r"不具合", r"急落", r"急騰"
]

MID_PATTERNS = [
    r"下方修正", r"減益", r"縮小", r"悪化", r"競争"
]

LONG_PATTERNS = [
    r"新規事業", r"戦略", r"規制", r"技術革新", r"M&A"
]


def detect_price_reaction(ticker: str) -> str:
    """
    ニュース後の株価反応を判定（急落なら短期ニュースの可能性が高い）
    """
    try:
        hist = get_stock_data(ticker, "7d")
        if hist is None or len(hist) < 3:
            return "unknown"

        # 直近3日の変化率
        close = hist["Close"].tail(3).values
        ret = (close[-1] - close[0]) / close[0]

        if ret <= -0.05:  # 5%以上の急落
            return "sharp_drop"
        if ret >= 0.05:
            return "sharp_rise"
        return "normal"
    except Exception:
        return "unknown"


def classify_news_duration(text: str, tickers: list) -> str:
    """
    ニュース本文＋株価反応から短期・中期・長期を分類（強化版）
    """
    if not text:
        return "mid"

    # 軸1：カテゴリ判定
    if any(k in text for k in LONG_CATEGORIES):
        return "long"
    if any(k in text for k in SHORT_CATEGORIES):
        return "short"
    if any(k in text for k in MID_CATEGORIES):
        return "mid"

    # 軸2：文章構造判定
    for pat in LONG_PATTERNS:
        if re.search(pat, text):
            return "long"
    for pat in MID_PATTERNS:
        if re.search(pat, text):
            return "mid"
    for pat in SHORT_PATTERNS:
        if re.search(pat, text):
            return "short"

    # 軸3：株価反応判定（短期ニュースの精度向上）
    for t in tickers:
        reaction = detect_price_reaction(t)
        if reaction == "sharp_drop":
            return "short"

    return "mid"


# ─────────────────────────────────────────
# 翻訳
# ─────────────────────────────────────────

def translate_to_ja(text: str) -> str:
    if not text:
        return text
    import re, requests
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
        return text


def translate_articles(articles: list) -> list:
    import time
    translated = []
    for i, a in enumerate(articles):
        a = dict(a)
        title = a.get("title", "")
        a["title_ja"] = translate_to_ja(title)
        translated.append(a)
        if i > 0 and i % 10 == 0:
            time.sleep(0.5)
    logger.info(f"翻訳完了: {len(translated)}件")
    return translated


# ─────────────────────────────────────────
# 感情分析
# ─────────────────────────────────────────

_vader = SentimentIntensityAnalyzer()

def analyze_sentiment(text: str) -> float:
    if not text:
        return 0.0

    english_ratio = len(re.findall(r'[a-zA-Z]', text)) / max(len(text), 1)
    if english_ratio > 0.3:
        scores = _vader.polarity_scores(text)
        return float(scores["compound"])

    return _japanese_sentiment(text)


def _japanese_sentiment(text: str) -> float:
    positive_words = [
        "上昇","増益","黒字","好調","拡大","成長","上方修正",
        "最高益","増配","自社株買い","好業績","需要増",
    ]
    negative_words = [
        "下落","減益","赤字","不振","縮小","下方修正",
        "最安値","減配","リストラ","業績悪化","需要減",
        "制裁","戦争","破綻","暴落","緊急",
    ]

    pos = sum(1 for w in positive_words if w in text)
    neg = sum(1 for w in negative_words if w in text)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


# ─────────────────────────────────────────
# セクター分類・危機検出・銘柄検出
# ─────────────────────────────────────────

def classify_sectors(text: str) -> list:
    text_lower = text.lower()
    matched = []
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(kw.lower() in text_lower for kw in keywords):
            matched.append(sector)
    return matched if matched else ["General"]


def detect_crisis_keywords(text: str) -> int:
    text_lower = text.lower()
    return sum(1 for kw in CRISIS_KEYWORDS if kw.lower() in text_lower)


TICKER_NAME_MAP = {
    "apple": "AAPL", "iphone": "AAPL",
    "microsoft": "MSFT", "azure": "MSFT",
    "google": "GOOGL", "alphabet": "GOOGL", "youtube": "GOOGL",
    "amazon": "AMZN", "aws": "AMZN",
    "nvidia": "NVDA", "cuda": "NVDA",
    "meta": "META", "facebook": "META", "instagram": "META",
    "tesla": "TSLA",
    "jpmorgan": "JPM", "jp morgan": "JPM",
    "johnson": "JNJ",
    "coca-cola": "KO", "coca cola": "KO",
    "berkshire": "BRK-B",
    "visa": "V",
    "walmart": "WMT",
    "netflix": "NFLX",
    "disney": "DIS",
    "トヨタ": "7203.T", "toyota": "7203.T",
    "ソニー": "6758.T", "sony": "6758.T",
    "ソフトバンク": "9984.T", "softbank": "9984.T",
    "任天堂": "7974.T", "nintendo": "7974.T",
    "武田": "4502.T", "takeda": "4502.T",
    "三菱UFJ": "8306.T", "mufg": "8306.T",
    "キーエンス": "6861.T", "keyence": "6861.T",
    "信越化学": "4063.T",
    "ファーストリテイリング": "9983.T", "ユニクロ": "9983.T", "uniqlo": "9983.T",
    "デンソー": "6902.T", "denso": "6902.T",
    "東京電力": "9501.T", "東電": "9501.T",
    "中部電力": "9502.T",
    "関西電力": "9503.T",
    "東京ガス": "9531.T",
    "大阪ガス": "9532.T",

    "アサヒ": "2502.T", "アサヒグループ": "2502.T",
    "キリン": "2503.T", "キリンHD": "2503.T",
    "キッコーマン": "2801.T",
    "味の素": "2802.T",
    "ニチレイ": "2871.T",

    "ケンコーマヨネーズ": "2915.T",

    "ヤマト": "9064.T", "ヤマトHD": "9064.T",
    "佐川": "9076.T", "SGHD": "9076.T",

    "三菱倉庫": "9301.T",
    "三井倉庫": "9302.T",

    "PR TIMES": "3922.T", "PRTIMES": "3922.T",
    "ミンカブ": "4436.T",
    "Sun Asterisk": "4053.T", "サンアスタ": "4053.T",
    "BASE": "4477.T",
    "メドレー": "4480.T",
    "ラクスル": "4384.T",
    "スマレジ": "4431.T",

    "ダブル・スコープ": "6619.T",
    "新光電気": "6967.T", "新光電工": "6967.T",

    "積水化学": "4204.T",
    "JSR": "4185.T",
    "大阪有機化学": "4187.T",
    "アイカ工業": "4206.T",

    "DMG森精機": "6141.T", "森精機": "6141.T",
    "クボタ": "6326.T",
    "荏原製作所": "6361.T", "荏原": "6361.T",
    "栗田工業": "6370.T",

    "日東電工": "6988.T",

    "ニコン": "7731.T",
    "トプコン": "7732.T",

    "日医工": "4541.T",
    "日本新薬": "4516.T"

}

def find_mentioned_tickers(text: str) -> list:
    text_lower = text.lower()
    found = set()
    for keyword, ticker in TICKER_NAME_MAP.items():
        if keyword.lower() in text_lower:
            found.add(ticker)
    return list(found)

# ─────────────────────────────────────────
# 短期悪材料判定（news_analyzer側の簡易版）
# ─────────────────────────────────────────

def is_short_term_negative(info: dict, ticker: str, sector_score: int, fundamentals: dict) -> bool:
    """
    news_analyzer側で短期悪材料を判定する簡易ロジック。
    scoring_engine側の詳細ロジックとは独立して動く。
    """

    # ① 危機キーワード（急性・慢性）がヒット
    if info.get("keyword_hit"):
        return True

    # ② センチメントが強いマイナス
    if info.get("sentiment", 0) <= -0.3:
        return True

    # ③ duration が short
    if info.get("duration") == "short":
        return True

    # ④ その他の軽い判定（必要なら拡張可能）
    # 例：sector_score が極端に低い場合など
    # if sector_score < 10:
    #     return True

    return False


# ─────────────────────────────────────────
# メイン解析
# ─────────────────────────────────────────

def analyze_articles(articles: list) -> dict:
    sector_sentiments  = defaultdict(list)
    ticker_sentiments  = defaultdict(list)
    ticker_durations   = defaultdict(list)
    crisis_total       = 0
    article_details    = []

    for article in articles:
        text      = article.get("text", "")
        tickers   = find_mentioned_tickers(text)
        sentiment = analyze_sentiment(text)
        duration  = classify_news_duration(text, tickers)
        sectors   = classify_sectors(text)
        crisis    = detect_crisis_keywords(text)

        crisis_total += crisis

        for sector in sectors:
            sector_sentiments[sector].append(sentiment)
        for ticker in tickers:
            ticker_sentiments[ticker].append(sentiment)
            ticker_durations[ticker].append(duration)
        
        # ───────────────────────────────
        # 短期悪材料判定（強化版）
        # ───────────────────────────────
        short_negative = False
        for t in tickers:
            sector_score = 15
            if sectors:
                sector_score = 15

            fundamentals = {}  # scoring_engine 側で使うので空でOK（後で埋める）

            if is_short_term_negative(
                {
                    "keyword_hit": crisis > 0,
                    "sentiment": sentiment,
                    "duration": duration,
                    "tickers": tickers,
                },
                t,
                sector_score,
                fundamentals
            ):
                short_negative = True
                break

        article_details.append({
            "title":     article.get("title", ""),
            "source":    article.get("source", ""),
            "sentiment": round(sentiment, 3),
            "duration":  duration,
            "sectors":   sectors,
            "tickers":   tickers,
            "crisis":    crisis,
            "published": article.get("published", ""),
            "is_short_negative": short_negative,   # ← 追加
        })


        article_details.append({
            "title":     article.get("title", ""),
            "source":    article.get("source", ""),
            "sentiment": round(sentiment, 3),
            "duration":  duration,
            "sectors":   sectors,
            "tickers":   tickers,
            "crisis":    crisis,
            "published": article.get("published", ""),
        })

    # セクター別平均スコア
    sector_scores = {}
    for sector, scores in sector_sentiments.items():
        avg = sum(scores) / len(scores)
        sector_scores[sector] = round((avg + 1) / 2 * 30, 1)

    # 銘柄別平均スコア
    ticker_scores = {}
    for ticker, scores in ticker_sentiments.items():
        avg = sum(scores) / len(scores)
        ticker_scores[ticker] = round((avg + 1) / 2 * 30, 1)

    # 銘柄別ニュース期間（多数決）
    ticker_duration_final = {}
    for ticker, durations in ticker_durations.items():
        if not durations:
            ticker_duration_final[ticker] = "mid"
        else:
            ticker_duration_final[ticker] = max(set(durations), key=durations.count)

    # 銘柄別センチメント
    ticker_sentiment_final = {}
    for ticker, scores in ticker_sentiments.items():
        ticker_sentiment_final[ticker] = sum(scores) / len(scores) if scores else 0.0

    # タイトル翻訳
    logger.info("記事タイトルを日本語翻訳中...")
    article_details = translate_articles(article_details)

    return {
        "sector_scores":   sector_scores,
        "ticker_scores":   ticker_scores,
        "ticker_duration": ticker_duration_final,
        "ticker_sentiment": ticker_sentiment_final,
        "crisis_count":    crisis_total,
        "acute_count":     0,
        "chronic_count":   crisis_total,
        "acute_keywords":  [],
        "top_positive":    [],
        "top_negative":    [],
        "article_count":   len(articles),
        "article_details": article_details[:80],
    }
# ─────────────────────────────────────────
# 銘柄ニューススコア取得（元のコードをそのまま復活）
# ─────────────────────────────────────────

def get_news_score_for_ticker(ticker: str, news_analysis: dict, sector: str = "") -> int:
    ticker_scores = news_analysis.get("ticker_scores", {})
    sector_scores = news_analysis.get("sector_scores", {})

    # 銘柄別スコアがある場合
    if ticker in ticker_scores:
        return int(ticker_scores[ticker])

    # セクター別スコアを代替として使用
    for s_key, s_score in sector_scores.items():
        if s_key.lower() in (sector or "").lower() or (sector or "").lower() in s_key.lower():
            return int(s_score)

    # デフォルト値
    return 15
