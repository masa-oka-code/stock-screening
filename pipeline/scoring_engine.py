"""
scoring_engine.py - 2段階スコアリング＋高配当/成長対応版（型ヒント完全版）
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Optional, Dict, Any, List

import pandas as pd
import yaml

from pipeline.market_data import (
    get_all_macro_data,
    get_fundamental_data,
    get_stock_data,
)
from pipeline.technical import (
    calc_technical_indicators,
    calc_technical_score,
)
from pipeline.fundamental import (
    calc_quality_score,
    calc_timing_score_fundamental,
    detect_bargain,
    get_extended_fundamental,
)
from pipeline.news_fetcher import fetch_all_news, calc_crisis_score
from pipeline.news_analyzer import analyze_articles, get_news_score_for_ticker

logger = logging.getLogger(__name__)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")


# ─────────────────────────────────────────
# 設定読み込み
# ─────────────────────────────────────────

def load_config() -> Dict[str, Any]:
    """config.yaml を読み込み、環境変数で上書きする（安全版）"""
    import yaml, os

    # .env 読み込み
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    # config.yaml 読み込み（安全版）
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = f.read()  # ← 文字列として読み込む
        cfg: Dict[str, Any] = yaml.safe_load(data) or {}  # ← safe_load は文字列に対して実行
    except Exception as e:
        print(f"config.yaml 読み込みエラー: {e}")
        cfg = {}

    # 環境変数で上書き
    cfg["newsapi_key"] = os.environ.get("NEWSAPI_KEY", cfg.get("newsapi_key", ""))

    email = cfg.setdefault("email", {})
    email["sender"]       = os.environ.get("EMAIL_SENDER",       email.get("sender", ""))
    email["receiver"]     = os.environ.get("EMAIL_RECEIVER",     email.get("receiver", ""))
    email["app_password"] = os.environ.get("EMAIL_APP_PASSWORD", email.get("app_password", ""))

    return cfg


# ─────────────────────────────────────────
# ユニバース読み込み
# ─────────────────────────────────────────

def load_universe(market: str) -> pd.DataFrame:
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "universe")
    path = os.path.join(base, f"universe_{market.lower()}.csv")

    for enc in ("utf-8-sig", "utf-8", "shift-jis", "cp932"):
        try:
            df = pd.read_csv(path, encoding=enc, sep=",", engine="python")
            if len(df.columns) >= 3:
                return df
        except Exception:
            continue

    return pd.read_csv(path, encoding="cp932", sep=",", engine="python")


# ─────────────────────────────────────────
# マクロフィルター（ゲートキーパー）
# ─────────────────────────────────────────

def calc_macro_score(macro: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    vix: float = macro.get("vix", 20)
    sp500: Dict[str, Any] = macro.get("sp500", {})
    nk225: Dict[str, Any] = macro.get("nk225", {})
    usdjpy: Dict[str, Any] = macro.get("usdjpy", {})
    cfg: Dict[str, Any] = config.get("macro", {})

    score: int = 0
    reasons: List[str] = []

    if vix >= cfg.get("vix_danger", 35):
        score -= 40
        reasons.append(f"VIX={vix:.0f}（危険水準）")
    elif vix >= cfg.get("vix_warning", 25):
        score -= 15
        reasons.append(f"VIX={vix:.0f}（警戒水準）")

    for label, trend in [("S&P500", sp500), ("日経225", nk225)]:
        ret: float = trend.get("ret_30d", 0)
        if ret <= -0.15:
            score -= 25
            reasons.append(f"{label} 急落({ret*100:.0f}%/月)")
        elif ret <= -0.08:
            score -= 12
            reasons.append(f"{label} 下落({ret*100:.0f}%/月)")
        if trend.get("dead_cross"):
            score -= 10
            reasons.append(f"{label} デッドクロス")

    fx_change: float = abs(usdjpy.get("change_1d", 0))
    if fx_change >= 2.0:
        score -= 10
        reasons.append(f"円急変動({fx_change:+.1f}円/日)")

    if score <= -60:
        risk_level = "CRITICAL"
    elif score <= -30:
        risk_level = "HIGH"
    elif score <= -15:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    sp_ret = sp500.get("ret_30d", 0)
    nk_ret = nk225.get("ret_30d", 0)
    bargain_mode = (
        vix >= cfg.get("vix_bargain_opportunity", 30)
        and (sp_ret <= -0.08 or nk_ret <= -0.08)
    )

    if score >= 0:
        macro_timing = 30
    elif score >= -15:
        macro_timing = 22
    elif score >= -30:
        macro_timing = 15
    elif score >= -45:
        macro_timing = 8
    else:
        macro_timing = 0

    return {
        "macro_score":   score,
        "macro_timing":  macro_timing,
        "risk_level":    risk_level,
        "bargain_mode":  bargain_mode,
        "suspend":       score <= cfg.get("macro_score_suspend", -60),
        "reasons":       reasons,
    }


# ─────────────────────────────────────────
# 短期ニュース監視ロジック（早買い防止）
# ─────────────────────────────────────────

def short_term_watch_logic(rsi: float, price: float, ma20: float) -> str:
    """
    短期悪材料ニュースが出た銘柄について、
    ・RSIが30未満 → まだ下落中 → 監視
    ・RSI30〜45 → 底打ち前 → 監視
    ・RSI>45 かつ 株価がMA20を上抜け → 反発開始 → 買い候補
    """
    if rsi < 30:
        return "watch"
    if 30 <= rsi <= 45:
        return "watch"
    if rsi > 45 and price > ma20:
        return "buy_candidate"
    return "watch"


# ─────────────────────────────────────────
# 高配当 / 成長スコア
# ─────────────────────────────────────────

def calc_dividend_score(fd: Dict[str, Any]) -> float:
    """配当利回りから高配当スコアを算出（最大10点）"""
    div = fd.get("div_yield", 0)
    return min(div * 100, 10.0)


def calc_growth_score(fd: Dict[str, Any]) -> float:
    """EPS成長率＋売上成長率から成長スコアを算出（最大20点）"""
    eps   = fd.get("eps_growth", 0)
    sales = fd.get("sales_growth", 0)
    return min(eps * 0.5 + sales * 0.5, 20.0)


# ─────────────────────────────────────────
# 銘柄スコアリング（2段階）
# ─────────────────────────────────────────

def score_single_stock(
    ticker: str,
    market: str,
    macro: Dict[str, Any],
    macro_result: Dict[str, Any],
    config: Dict[str, Any],
    news_score: int = 15,
    sector_score: int = 10,
    news_duration: str = "mid",
    news_sentiment: float = 0.0
) -> Optional[Dict[str, Any]]:

    # 基本財務データ
    fd: Dict[str, Any] = get_fundamental_data(ticker)
    if "error" in fd or not fd.get("current_price"):
        return None

    # 株価フィルター
    cfg_f = config.get("filters", {})
    price: float = fd.get("current_price", 0)

    if market == "JP":
        if not (cfg_f.get("min_price_jpy", 0) <= price <= cfg_f.get("max_price_jpy", 999999)):
            return None
    else:
        if not (cfg_f.get("min_price_usd", 0) <= price <= cfg_f.get("max_price_usd", 99999)):
            return None

    # 拡張データ（連続増配・配当落ち日など）
    ext = get_extended_fundamental(ticker)
    fd.update(ext)

    # ── 第1スコア：品質（100点換算）──
    quality: Dict[str, Any] = calc_quality_score(fd, market)

    quality_total: float = (
        quality["score"] / 65 * 65 +
        news_score / 30 * 15 +
        sector_score / 10 * 20
    )

    # ── 第2スコア：タイミング（100点換算）──
    raw_timing_fund = calc_timing_score_fundamental(fd, market)
    timing_fund: Dict[str, Any] = calc_timing_score_fundamental(fd, market)

    raw_hist = get_stock_data(ticker, "6mo")
    hist = get_stock_data(ticker, "6mo")

    if hist is not None:
        indicators: Dict[str, float] = calc_technical_indicators(hist)
        tech_result: Dict[str, Any] = calc_technical_score(indicators)

        tech_timing: float = tech_result["score"] / 20 * 20
        rsi: float = tech_result.get("rsi", 50)
        ma20: float = indicators.get("ma20", price)
    else:
        tech_result: Dict[str, Any] = {
            "score": 10.0,
            "breakdown": {},
            "comment": "データなし",
            "rsi": 50.0,
        }
        tech_timing = 10.0
        rsi = 50.0
        ma20 = price

    macro_timing: float = macro_result["macro_timing"]

    timing_total: float = timing_fund["score"] + macro_timing + tech_timing

    # ── 短期悪材料ニュースならウォッチロジック適用（改善版） ──
    if news_duration == "short" and news_sentiment < 0:

        status = short_term_watch_logic(rsi, price, ma20)

        # ① 反転前 → ウォッチ銘柄として残す（除外しない）
        if status == "watch":
            return {
                "ticker": ticker,
                "market": market,
                "name": "",
                "sector": fd.get("sector", ""),
                "current_price": price,
                "status": "watch",               # ← NEW
                "reason": "短期悪材料（反転前）",  # ← NEW
                "rsi": rsi,
                "ma20": ma20,
                "sentiment": news_sentiment,
                "duration": news_duration,
            }

        # ② 反転シグナル → 健全企業なら候補に復帰
        if status == "buy_candidate":
            if quality_total >= 60:  # ← 健全企業判定
                # 反転したので通常スコアリングへ進む（何も return しない）
                pass
            else:
                # 健全でない企業はウォッチに留める
                return {
                    "ticker": ticker,
                    "market": market,
                    "name": "",
                    "sector": fd.get("sector", ""),
                    "current_price": price,
                    "status": "watch",
                    "reason": "短期悪材料（企業健全性不足）",
                    "rsi": rsi,
                    "ma20": ma20,
                    "sentiment": news_sentiment,
                    "duration": news_duration,
                }


    # ── 総合スコア ──
    total_score: float = round(quality_total * 0.6 + timing_total * 0.4, 1)
    total_score = max(0, min(100, total_score))

    # バーゲン検知
    bargain: Dict[str, Any] = detect_bargain(fd, quality, macro, market)
    if bargain["is_bargain"]:
        bargain_bonus = min(15, bargain["bargain_score"] / 6)
        total_score = min(100, total_score + bargain_bonus)

    # 判定区分
    if bargain["is_bargain"]:
        judgment = "BARGAIN"
    elif quality_total >= 70 and timing_total >= 55:
        judgment = "積極推薦"
    elif quality_total >= 65 and timing_total < 40:
        judgment = "要観察"
    elif quality_total >= 60:
        judgment = "候補"
    else:
        judgment = "参考"

    # 閾値チェック
    threshold: float = (
        config["scoring"].get("bargain_mode_score", 45)
        if macro_result["bargain_mode"]
        else config["scoring"].get("min_total_score", 55)
    )

    if total_score < threshold and not bargain["is_bargain"] and judgment not in ("積極推薦", "BARGAIN"):
        return None

    # 高配当 / 成長スコア
    div_score: float = calc_dividend_score(fd)
    growth_score: float = calc_growth_score(fd)

    total_score_dividend: float = (
        quality_total * 0.5 +
        div_score * 0.3 +
        timing_total * 0.2
    )

    total_score_growth: float = (
        quality_total * 0.5 +
        growth_score * 0.3 +
        timing_total * 0.2
    )

    return {
        "ticker":         ticker,
        "market":         market,
        "name":           "",
        "sector":         fd.get("sector", ""),
        "current_price":  price,
        "total_score":    round(total_score, 1),
        "total_score_dividend": round(total_score_dividend, 1),
        "total_score_growth":   round(total_score_growth, 1),
        "judgment":       str(judgment),
        "quality_score":  float(round(quality_total, 1)),
        "timing_score":   round(timing_total, 1),
        "breakdown": {
            "quality": {
                "fundamental": quality["score"],
                "news":        round(news_score / 30 * 15, 1),
                "sector":      round(sector_score / 10 * 20, 1),
                "total":       round(quality_total, 1),
            },
            "timing": {
                "valuation":   timing_fund["score"],
                "macro":       macro_timing,
                "technical":   round(tech_timing, 1),
                "total":       round(timing_total, 1),
            },
        },
        "is_bargain":      bargain["is_bargain"],
        "bargain_score":   bargain["bargain_score"],
        "bargain_reasons": bargain["reasons"],
        "fund_comment":    quality["comment"],
        "timing_comment":  timing_fund["comment"],
        "tech_comment":    tech_result.get("comment", ""),
        "rsi":             rsi,
        "div_yield":       fd.get("div_yield", 0),
        "pe":              quality.get("pe"),
        "pbr":             quality.get("pbr"),
        "consecutive_div": fd.get("consecutive_dividend_years", 0),
        "ex_div_date":     str(fd.get("ex_dividend_date", "")) or "",
        "comment":         quality["comment"],
    }



# ─────────────────────────────────────────
# メインパイプライン
# ─────────────────────────────────────────

def run_pipeline() -> Dict[str, Any]:
    config: Dict[str, Any] = load_config()
    logger.info("=" * 50)
    logger.info("パイプライン開始")
    logger.info("=" * 50)

    logger.info("ニュース取得中...")
    articles: List[Dict[str, Any]] = fetch_all_news(config)
    news_analysis: Dict[str, Any] = analyze_articles(articles)
    crisis: Dict[str, Any] = calc_crisis_score(articles)

    logger.info(
        "ニュース解析完了: " + str(len(articles)) + "件"
        + "  急性KW:" + str(crisis["acute_count"])
        + "  慢性KW:" + str(crisis["chronic_count"])
    )

    macro: Dict[str, Any] = get_all_macro_data()
    macro_result: Dict[str, Any] = calc_macro_score(macro, config)

    # マクロ危機補正
    if crisis["crisis_score"] < 0:
        macro_result["macro_score"] += crisis["crisis_score"]
        macro_result["macro_timing"] = max(
            0,
            macro_result["macro_timing"] + crisis["crisis_score"] // 2
        )

        if crisis["acute_count"] > 0:
            macro_result["reasons"].append(
                "急性危機KW検出(" + str(crisis["acute_count"]) + "件)"
            )
        if crisis["chronic_surge"]:
            macro_result["reasons"].append("慢性リスク急増")

        if macro_result["macro_score"] <= -30:
            macro_result["risk_level"] = "HIGH"

    logger.info(
        "マクロリスク: " + macro_result["risk_level"]
        + "  スコア:" + str(macro_result["macro_score"])
        + "  バーゲンモード:" + str(macro_result["bargain_mode"])
    )

    if macro_result["suspend"]:
        logger.warning("マクロリスクCRITICAL：スクリーニング停止")
        result = _build_result(macro, macro_result, [], config, news_analysis, crisis)
        _save_result(result)
        return result

    # ユニバース読み込み
    universes: List[pd.DataFrame] = []
    if config["markets"].get("us"):
        df_us = load_universe("us")
        df_us["market"] = "US"
        universes.append(df_us)

    if config["markets"].get("japan"):
        df_jp = load_universe("jp")
        df_jp["market"] = "JP"
        universes.append(df_jp)

    all_stocks: pd.DataFrame = pd.concat(universes, ignore_index=True)
    logger.info("スクリーニング対象: " + str(len(all_stocks)) + "銘柄")

    candidates: List[Dict[str, Any]] = []
    errors: int = 0

    # ★ tickerごとのニュース期間・センチメントを統合
    ticker_duration: Dict[str, str] = news_analysis.get("ticker_duration", {})
    ticker_sentiment: Dict[str, float] = news_analysis.get("ticker_sentiment", {})

    for i, row in all_stocks.iterrows():
        ticker: str = row["ticker"]
        market: str = row["market"]

        if i % 10 == 0:
            logger.info(
                "  スコアリング中... "
                + str(i) + "/" + str(len(all_stocks))
                + "  候補:" + str(len(candidates))
            )

        try:
            sector_hint: str = row.get("sector", "")
            news_score: int = get_news_score_for_ticker(ticker, news_analysis, sector=sector_hint)

            # セクタースコア（ニュースのセクタースコアを流用、10点換算）
            sector_news: float = news_analysis.get("sector_scores", {}).get(sector_hint, 15)
            sector_score: int = int(sector_news / 30 * 10)

            # ★ tickerごとのニュース期間・センチメント
            duration: str = ticker_duration.get(ticker, "mid")
            sentiment: float = ticker_sentiment.get(ticker, 0.0)

            result: Optional[Dict[str, Any]] = score_single_stock(
                ticker,
                market,
                macro,
                macro_result,
                config,
                news_score=news_score,
                sector_score=sector_score,
                news_duration=duration,
                news_sentiment=sentiment,
            )

            if result:
                result["name"] = row.get("name", ticker)
                result["sector"] = result["sector"] or sector_hint
                candidates.append(result)

        except Exception as e:
            errors += 1
            logger.debug("  " + ticker + " スキップ: " + str(e))

        time.sleep(0.2)

    # ソート
    priority: Dict[str, int] = {
        "BARGAIN": 0,
        "積極推薦": 1,
        "候補": 2,
        "要観察": 3,
        "参考": 4,
    }

    candidates.sort(
        key=lambda x: (
            priority.get(x.get("judgment", "参考"), 4),
            -x["total_score"],
        )
    )

    logger.info(
        "スクリーニング完了: "
        + str(len(candidates))
        + "銘柄  エラー:"
        + str(errors)
    )

    result: Dict[str, Any] = _build_result(
        macro,
        macro_result,
        candidates,
        config,
        news_analysis,
        crisis,
    )

    _save_result(result)
    return result


# ─────────────────────────────────────────
# 結果構築
# ─────────────────────────────────────────

def _build_result(
    macro: Dict[str, Any],
    macro_result: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    config: Dict[str, Any],
    news_analysis: Optional[Dict[str, Any]] = None,
    crisis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(),
        "macro": {
            "vix":          macro["vix"],
            "sp500_30d":    macro["sp500"]["ret_30d"],
            "nk225_30d":    macro["nk225"]["ret_30d"],
            "usdjpy":       macro["usdjpy"]["rate"],
            "risk_level":   macro_result["risk_level"],
            "macro_score":  macro_result["macro_score"],
            "macro_timing": macro_result["macro_timing"],
            "bargain_mode": macro_result["bargain_mode"],
            "suspend":      macro_result["suspend"],
            "reasons":      macro_result["reasons"],
        },
        "candidates":      candidates,
        "bargain_picks":   [c for c in candidates if c.get("is_bargain")],
        "candidate_count": len(candidates),
        "news": {
            "article_count":   (news_analysis or {}).get("article_count", 0),
            "crisis_count":    (news_analysis or {}).get("crisis_count", 0),
            "acute_count":     (crisis or {}).get("acute_count", 0),
            "chronic_count":   (crisis or {}).get("chronic_count", 0),
            "acute_keywords":  (news_analysis or {}).get("acute_keywords", []),
            "sector_scores":   (news_analysis or {}).get("sector_scores", {}),
            "top_positive":    (news_analysis or {}).get("top_positive", []),
            "top_negative":    (news_analysis or {}).get("top_negative", []),
            "ticker_scores":   (news_analysis or {}).get("ticker_scores", {}),
            "article_details": (news_analysis or {}).get("article_details", []),
        },
    }


# ─────────────────────────────────────────
# 結果保存
# ─────────────────────────────────────────

def _save_result(result: Dict[str, Any]) -> None:
    out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "results")
    os.makedirs(out_dir, exist_ok=True)

    path = os.path.join(out_dir, result["date"].replace("-", "") + ".json")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logger.info("結果保存: " + path)


# ─────────────────────────────────────────
# テスト実行
# ─────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    test_tickers: List[str] = ["KO", "JNJ", "7203.T", "4502.T", "AAPL"]
    logger.info("テストモード: " + str(test_tickers))

    config: Dict[str, Any] = load_config()

    logger.info("ニュース取得中...")
    articles: List[Dict[str, Any]] = fetch_all_news(config)
    news_analysis: Dict[str, Any] = analyze_articles(articles)
    crisis: Dict[str, Any] = calc_crisis_score(articles)

    macro: Dict[str, Any] = get_all_macro_data()
    macro_result: Dict[str, Any] = calc_macro_score(macro, config)

    if crisis["crisis_score"] < 0:
        macro_result["macro_score"] += crisis["crisis_score"]
        macro_result["macro_timing"] = max(
            0,
            macro_result["macro_timing"] + crisis["crisis_score"] // 2
        )

    print("\n【マクロ判定】")
    print(
        "  リスク: " + macro_result["risk_level"]
        + "  タイミング点: " + str(macro_result["macro_timing"]) + "/30"
    )
    print("  理由: " + (" / ".join(macro_result["reasons"]) or "なし"))

    print("\n【銘柄スコアリング（2段階＋高配当/成長）】")

    for ticker in test_tickers:
        market: str = "JP" if ticker.endswith(".T") else "US"
        news_score: int = get_news_score_for_ticker(ticker, news_analysis)

        r: Optional[Dict[str, Any]] = score_single_stock(
            ticker,
            market,
            macro,
            macro_result,
            config,
            news_score=news_score,
        )

        if r:
            bd = r["breakdown"]

            print(
                f"\n  {ticker} [{r['judgment']}] "
                f"総合:{r['total_score']} / 高配当:{r['total_score_dividend']} / 成長:{r['total_score_growth']}"
            )

            print(
                f"    品質:{r['quality_score']:.0f}/100  "
                f"(ファンダ:{bd['quality']['fundamental']}/65 "
                f"ニュース:{bd['quality']['news']:.0f}/15 "
                f"セクター:{bd['quality']['sector']:.0f}/20)"
            )

            print(
                f"    タイミング:{r['timing_score']:.0f}/100  "
                f"(割安感:{bd['timing']['valuation']}/50 "
                f"マクロ:{bd['timing']['macro']}/30 "
                f"テクニカル:{bd['timing']['technical']:.0f}/20)"
            )

            print(f"    → {r['comment']}")

            if r.get("timing_comment"):
                print(f"    タイミング: {r['timing_comment']}")

            if r.get("ex_div_date"):
                print(f"    配当落ち日: {r['ex_div_date'][:10]}")

        else:
            print(f"\n  {ticker} → 閾値未達・スキップ")

        time.sleep(0.3)
