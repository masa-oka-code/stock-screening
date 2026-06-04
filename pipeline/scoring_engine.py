"""
scoring_engine.py - 2段階スコアリング対応版
第1スコア（品質）: ファンダ65点 + セクター20点 + ニュース15点 = 100点
第2スコア（タイミング）: 割安感50点 + マクロ30点 + テクニカル20点 = 100点
総合: 品質×0.6 + タイミング×0.4
"""

import json, logging, os, time
from datetime import datetime
from typing import Optional

import pandas as pd
import yaml

from pipeline.market_data import get_all_macro_data, get_fundamental_data, get_stock_data
from pipeline.technical import calc_technical_indicators, calc_technical_score
from pipeline.fundamental import (
    calc_quality_score, calc_timing_score_fundamental,
    detect_bargain, get_extended_fundamental
)
from pipeline.news_fetcher import fetch_all_news, calc_crisis_score
from pipeline.news_analyzer import analyze_articles, get_news_score_for_ticker

logger = logging.getLogger(__name__)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")


def load_config() -> dict:
    """
    config.yaml を読み込み、"ENV" の値を環境変数で上書きする。
    ローカル開発時は .env ファイル、GitHub Actions では Secrets が使われる。
    """
    import os
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # 環境変数でシークレットを上書き（"ENV" プレースホルダーを置換）
    cfg["newsapi_key"] = os.environ.get("NEWSAPI_KEY", cfg.get("newsapi_key", ""))
    email = cfg.setdefault("email", {})
    email["sender"]       = os.environ.get("EMAIL_SENDER",       email.get("sender", ""))
    email["receiver"]     = os.environ.get("EMAIL_RECEIVER",     email.get("receiver", ""))
    email["app_password"] = os.environ.get("EMAIL_APP_PASSWORD", email.get("app_password", ""))

    return cfg


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

def calc_macro_score(macro: dict, config: dict) -> dict:
    vix    = macro.get("vix", 20)
    sp500  = macro.get("sp500", {})
    nk225  = macro.get("nk225", {})
    usdjpy = macro.get("usdjpy", {})
    cfg    = config.get("macro", {})

    score, reasons = 0, []

    if vix >= cfg.get("vix_danger", 35):
        score -= 40; reasons.append(f"VIX={vix:.0f}（危険水準）")
    elif vix >= cfg.get("vix_warning", 25):
        score -= 15; reasons.append(f"VIX={vix:.0f}（警戒水準）")

    for label, trend in [("S&P500", sp500), ("日経225", nk225)]:
        ret = trend.get("ret_30d", 0)
        if ret <= -0.15:   score -= 25; reasons.append(f"{label} 急落({ret*100:.0f}%/月)")
        elif ret <= -0.08: score -= 12; reasons.append(f"{label} 下落({ret*100:.0f}%/月)")
        if trend.get("dead_cross"): score -= 10; reasons.append(f"{label} デッドクロス")

    fx_change = abs(usdjpy.get("change_1d", 0))
    if fx_change >= 2.0:
        score -= 10; reasons.append(f"円急変動({fx_change:+.1f}円/日)")

    if score <= -60:   risk_level = "CRITICAL"
    elif score <= -30: risk_level = "HIGH"
    elif score <= -15: risk_level = "MEDIUM"
    else:              risk_level = "LOW"

    sp_ret = sp500.get("ret_30d", 0)
    nk_ret = nk225.get("ret_30d", 0)
    bargain_mode = (
        vix >= cfg.get("vix_bargain_opportunity", 30)
        and (sp_ret <= -0.08 or nk_ret <= -0.08)
    )

    # タイミングスコアへの変換（0〜30点）
    # マクロが良好なほど高得点
    if score >= 0:       macro_timing = 30
    elif score >= -15:   macro_timing = 22
    elif score >= -30:   macro_timing = 15
    elif score >= -45:   macro_timing = 8
    else:                macro_timing = 0

    return {
        "macro_score":   score,
        "macro_timing":  macro_timing,   # タイミングスコアへの寄与
        "risk_level":    risk_level,
        "bargain_mode":  bargain_mode,
        "suspend":       score <= cfg.get("macro_score_suspend", -60),
        "reasons":       reasons,
    }


# ─────────────────────────────────────────
# 銘柄スコアリング（2段階）
# ─────────────────────────────────────────

def score_single_stock(ticker: str, market: str, macro: dict,
                        macro_result: dict, config: dict,
                        news_score: int = 15,
                        sector_score: int = 10) -> Optional[dict]:
    # 基本財務データ
    fd = get_fundamental_data(ticker)
    if "error" in fd or not fd.get("current_price"):
        return None

    # 株価フィルター
    cfg_f = config.get("filters", {})
    price = fd.get("current_price", 0)
    if market == "JP":
        if not (cfg_f.get("min_price_jpy", 0) <= price <= cfg_f.get("max_price_jpy", 999999)):
            return None
    else:
        if not (cfg_f.get("min_price_usd", 0) <= price <= cfg_f.get("max_price_usd", 99999)):
            return None

    # 拡張データ（連続増配・配当落ち日・粗利益率）
    ext = get_extended_fundamental(ticker)
    fd.update(ext)

    # ── 第1スコア：品質（100点換算）──
    quality = calc_quality_score(fd, market)

    # ニュースとセクターを品質に統合
    # ニュース15点満点、セクター20点満点 → 合計100点になるよう正規化
    quality_total = (
        quality["score"] / 65 * 65   # ファンダ65点
        + news_score / 30 * 15       # ニュース15点
        + sector_score / 10 * 20     # セクター20点
    )  # 合計100点満点

    # ── 第2スコア：タイミング（100点換算）──
    # ファンダ割安感50点
    timing_fund = calc_timing_score_fundamental(fd, market)

    # テクニカル20点（トレンド確認用）
    hist = get_stock_data(ticker, "6mo")
    if hist is not None:
        indicators  = calc_technical_indicators(hist)
        tech_result = calc_technical_score(indicators)
        # テクニカル20点に正規化
        tech_timing = tech_result["score"] / 20 * 20
    else:
        tech_result = {"score": 10, "breakdown": {}, "comment": "データなし", "rsi": 50}
        tech_timing = 10

    # マクロ30点
    macro_timing = macro_result["macro_timing"]

    timing_total = timing_fund["score"] + macro_timing + tech_timing  # 最大100点

    # ── 総合スコア ──
    total_score = round(quality_total * 0.6 + timing_total * 0.4, 1)
    total_score = max(0, min(100, total_score))

    # バーゲン検知
    bargain = detect_bargain(fd, quality, macro, market)
    if bargain["is_bargain"]:
        bargain_bonus = min(15, bargain["bargain_score"] / 6)
        total_score   = min(100, total_score + bargain_bonus)

    # 判定区分
    if bargain["is_bargain"]:
        judgment = "BARGAIN"
    elif quality_total >= 70 and timing_total >= 55:
        judgment = "積極推薦"
    elif quality_total >= 65 and timing_total < 40:
        judgment = "要観察"   # 良い銘柄だが今は割高
    elif quality_total >= 60:
        judgment = "候補"
    else:
        judgment = "参考"

    # 閾値チェック
    threshold = (
        config["scoring"].get("bargain_mode_score", 45)
        if macro_result["bargain_mode"]
        else config["scoring"].get("min_total_score", 55)
    )
    if total_score < threshold and not bargain["is_bargain"] and judgment not in ("積極推薦", "BARGAIN"):
        return None

    return {
        "ticker":         ticker,
        "market":         market,
        "name":           "",
        "sector":         fd.get("sector", ""),
        "current_price":  price,
        "total_score":    total_score,
        "judgment":       judgment,
        # 2段階スコア
        "quality_score":  round(quality_total, 1),
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
        "rsi":             tech_result.get("rsi", 50),
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

def run_pipeline() -> dict:
    config = load_config()
    logger.info("=" * 50)
    logger.info("パイプライン開始")
    logger.info("=" * 50)

    logger.info("ニュース取得中...")
    articles      = fetch_all_news(config)
    news_analysis = analyze_articles(articles)
    crisis        = calc_crisis_score(articles)

    logger.info("ニュース解析完了: " + str(len(articles)) + "件" +
                "  急性KW:" + str(crisis["acute_count"]) +
                "  慢性KW:" + str(crisis["chronic_count"]))

    macro        = get_all_macro_data()
    macro_result = calc_macro_score(macro, config)

    if crisis["crisis_score"] < 0:
        macro_result["macro_score"]  += crisis["crisis_score"]
        macro_result["macro_timing"] = max(0, macro_result["macro_timing"] + crisis["crisis_score"] // 2)
        if crisis["acute_count"] > 0:
            macro_result["reasons"].append("急性危機KW検出(" + str(crisis["acute_count"]) + "件)")
        if crisis["chronic_surge"]:
            macro_result["reasons"].append("慢性リスク急増")
        if macro_result["macro_score"] <= -30:
            macro_result["risk_level"] = "HIGH"

    logger.info("マクロリスク: " + macro_result["risk_level"] +
                "  スコア:" + str(macro_result["macro_score"]) +
                "  バーゲンモード:" + str(macro_result["bargain_mode"]))

    if macro_result["suspend"]:
        logger.warning("マクロリスクCRITICAL：スクリーニング停止")
        result = _build_result(macro, macro_result, [], config, news_analysis, crisis)
        _save_result(result)
        return result

    universes = []
    if config["markets"].get("us"):
        df = load_universe("us"); df["market"] = "US"; universes.append(df)
    if config["markets"].get("japan"):
        df = load_universe("jp"); df["market"] = "JP"; universes.append(df)

    all_stocks = pd.concat(universes, ignore_index=True)
    logger.info("スクリーニング対象: " + str(len(all_stocks)) + "銘柄")

    candidates, errors = [], 0
    for i, row in all_stocks.iterrows():
        ticker = row["ticker"]
        market = row["market"]
        if i % 10 == 0:
            logger.info("  スコアリング中... " + str(i) + "/" + str(len(all_stocks)) +
                        "  候補:" + str(len(candidates)))
        try:
            sector_hint  = row.get("sector", "")
            news_score   = get_news_score_for_ticker(ticker, news_analysis, sector=sector_hint)
            # セクタースコア（ニュースのセクタースコアを流用、10点換算）
            sector_news  = news_analysis.get("sector_scores", {}).get(sector_hint, 15)
            sector_score = int(sector_news / 30 * 10)

            result = score_single_stock(
                ticker, market, macro, macro_result, config,
                news_score=news_score, sector_score=sector_score
            )
            if result:
                result["name"]   = row.get("name", ticker)
                result["sector"] = result["sector"] or sector_hint
                candidates.append(result)
        except Exception as e:
            errors += 1
            logger.debug("  " + ticker + " スキップ: " + str(e))
        time.sleep(0.2)

    # 積極推薦・バーゲンを優先してソート
    priority = {"BARGAIN": 0, "積極推薦": 1, "候補": 2, "要観察": 3, "参考": 4}
    candidates.sort(key=lambda x: (
        priority.get(x.get("judgment","参考"), 4),
        -x["total_score"]
    ))

    logger.info("スクリーニング完了: " + str(len(candidates)) + "銘柄  エラー:" + str(errors))
    result = _build_result(macro, macro_result, candidates, config, news_analysis, crisis)
    _save_result(result)
    return result


def _build_result(macro, macro_result, candidates, config, news_analysis=None, crisis=None) -> dict:
    return {
        "date":          datetime.now().strftime("%Y-%m-%d"),
        "generated_at":  datetime.now().isoformat(),
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


def _save_result(result: dict):
    out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "results")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, result["date"].replace("-","") + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info("結果保存: " + path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    test_tickers = ["KO", "JNJ", "7203.T", "4502.T", "AAPL"]
    logger.info("テストモード: " + str(test_tickers))
    config = load_config()

    logger.info("ニュース取得中...")
    articles      = fetch_all_news(config)
    news_analysis = analyze_articles(articles)
    crisis        = calc_crisis_score(articles)

    macro        = get_all_macro_data()
    macro_result = calc_macro_score(macro, config)
    if crisis["crisis_score"] < 0:
        macro_result["macro_score"]  += crisis["crisis_score"]
        macro_result["macro_timing"] = max(0, macro_result["macro_timing"] + crisis["crisis_score"] // 2)

    print("\n【マクロ判定】")
    print("  リスク: " + macro_result["risk_level"] + "  タイミング点: " + str(macro_result["macro_timing"]) + "/30")
    print("  理由: " + (" / ".join(macro_result["reasons"]) or "なし"))

    print("\n【銘柄スコアリング（2段階）】")
    for ticker in test_tickers:
        market     = "JP" if ticker.endswith(".T") else "US"
        news_score = get_news_score_for_ticker(ticker, news_analysis)
        r = score_single_stock(ticker, market, macro, macro_result, config, news_score=news_score)
        if r:
            bd = r["breakdown"]
            print(f"\n  {ticker} [{r['judgment']}] 総合:{r['total_score']}")
            print(f"    品質:{r['quality_score']:.0f}/100  (ファンダ:{bd['quality']['fundamental']}/65 ニュース:{bd['quality']['news']:.0f}/15 セクター:{bd['quality']['sector']:.0f}/20)")
            print(f"    タイミング:{r['timing_score']:.0f}/100  (割安感:{bd['timing']['valuation']}/50 マクロ:{bd['timing']['macro']}/30 テクニカル:{bd['timing']['technical']:.0f}/20)")
            print(f"    → {r['comment']}")
            if r.get("timing_comment"):
                print(f"    タイミング: {r['timing_comment']}")
            if r.get("ex_div_date"):
                print(f"    配当落ち日: {r['ex_div_date'][:10]}")
        else:
            print(f"\n  {ticker} → 閾値未達・スキップ")
        time.sleep(0.3)