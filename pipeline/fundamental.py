"""
fundamental.py
ファンダメンタル指標のスコアリング（2段階構造）

第1スコア: 銘柄品質スコア（65点満点）
  - バリュエーション 20点
  - 成長性         20点
  - 財務安定性     15点
  - 株主還元       10点

第2スコア: 買いタイミングスコア（100点満点・fundamental側の担当分）
  - 割安感（52週位置・PER乖離） 50点
  ※ マクロ30点・テクニカル20点はscoring_engine側で合算
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

HISTORICAL_AVG_PER = {"US": 22.0, "JP": 15.0}

SECTOR_DE_THRESHOLD = {
    "銀行業": 2000, "保険業": 1000, "輸送用機器": 200,
    "陸運業": 300, "不動産業": 300, "電気・ガス業": 200,
    "Financials": 2000, "Real Estate": 300, "Utilities": 300,
    "Consumer Discretionary": 200, "_default": 150,
}

def _get_de_threshold(sector: str) -> float:
    for key, threshold in SECTOR_DE_THRESHOLD.items():
        if key != "_default" and key in (sector or ""):
            return threshold
    return SECTOR_DE_THRESHOLD["_default"]


# ─────────────────────────────────────────
# 第1スコア：銘柄品質
# ─────────────────────────────────────────

def calc_quality_score(fd: dict, market: str = "US") -> dict:
    """
    銘柄品質スコアを算出（65点満点）
    「この会社は長期的に信頼できるか」を評価
    """
    if not fd or "error" in fd:
        return {"score": 0, "breakdown": {}, "comment": "データ不足", "stability_score": 0}

    trailing_pe     = fd.get("trailing_pe")
    forward_pe      = fd.get("forward_pe")
    pbr             = fd.get("pbr")
    roe             = fd.get("roe")
    div_yield       = fd.get("div_yield") or 0
    revenue_growth  = fd.get("revenue_growth")
    earnings_growth = fd.get("earnings_growth")
    debt_to_equity  = fd.get("debt_to_equity")
    current_ratio   = fd.get("current_ratio")
    sector          = fd.get("sector", "")
    gross_margins   = fd.get("gross_margins")
    operating_cf    = fd.get("operating_cashflow")
    total_revenue   = fd.get("total_revenue")
    consecutive_div = fd.get("consecutive_dividend_years", 0) or 0

    avg_per     = HISTORICAL_AVG_PER.get(market, 20.0)
    score       = 0
    breakdown   = {}
    reasons     = []

    # ── バリュエーション（20点）──
    val = 0
    pe  = forward_pe or trailing_pe
    if pe is not None:
        if pe < avg_per * 0.70:
            val += 10; reasons.append(f"PER割安({pe:.1f})")
        elif pe < avg_per:
            val += 6;  reasons.append(f"PER適正({pe:.1f})")
        elif pe < avg_per * 1.3:
            val += 2
        else:
            val -= 2;  reasons.append(f"PER割高({pe:.1f})")

    if pbr is not None:
        if pbr < 1.0:   val += 6; reasons.append(f"PBR<1({pbr:.1f})")
        elif pbr < 2.0: val += 3; reasons.append(f"PBR低({pbr:.1f})")
        elif pbr > 8.0: val -= 1

    if div_yield >= 0.05:    val += 4; reasons.append(f"高配当{div_yield*100:.1f}%")
    elif div_yield >= 0.03:  val += 3; reasons.append(f"配当{div_yield*100:.1f}%")
    elif div_yield >= 0.015: val += 1

    val = max(0, min(20, val))
    breakdown["valuation"] = val
    score += val

    # ── 成長性（20点）──
    grow = 0
    if revenue_growth is not None:
        if revenue_growth > 0.20:   grow += 8; reasons.append(f"売上+{revenue_growth*100:.0f}%")
        elif revenue_growth > 0.10: grow += 5; reasons.append(f"売上+{revenue_growth*100:.0f}%")
        elif revenue_growth > 0.03: grow += 2
        elif revenue_growth < 0:    grow -= 2; reasons.append("売上減⚠")

    if earnings_growth is not None:
        if earnings_growth > 0.20:    grow += 10; reasons.append(f"利益+{earnings_growth*100:.0f}%")
        elif earnings_growth > 0.10:  grow += 6
        elif earnings_growth < -0.10: grow -= 3;  reasons.append("利益減⚠")

    # 粗利益率（競争優位性の代理指標）
    if gross_margins is not None:
        if gross_margins > 0.50:   grow += 2; reasons.append(f"粗利率高({gross_margins*100:.0f}%)")
        elif gross_margins > 0.30: grow += 1

    grow = max(0, min(20, grow))
    breakdown["growth"] = grow
    score += grow

    # ── 財務安定性（15点）──
    stab       = 0
    de_thresh  = _get_de_threshold(sector)

    if roe is not None:
        if roe > 0.20:   stab += 5; reasons.append(f"ROE高({roe*100:.0f}%)")
        elif roe > 0.10: stab += 3; reasons.append(f"ROE({roe*100:.0f}%)")
        elif roe < 0:    stab -= 2

    if debt_to_equity is not None:
        de_ratio = debt_to_equity / de_thresh
        if de_ratio < 0.5:   stab += 5; reasons.append("財務健全")
        elif de_ratio < 1.0: stab += 3
        elif de_ratio > 1.5: stab -= 2; reasons.append("高負債⚠")
    else:
        stab += 1

    if current_ratio is not None:
        if current_ratio > 2.0:   stab += 4
        elif current_ratio > 1.5: stab += 2
        elif current_ratio > 1.0: stab += 1
        else: stab -= 1; reasons.append("流動性低⚠")
    else:
        stab += 1

    # 営業CF がプラスかチェック
    if operating_cf is not None and total_revenue is not None and total_revenue > 0:
        cf_margin = operating_cf / total_revenue
        if cf_margin > 0.15:   stab += 1
        elif cf_margin < 0:    stab -= 1; reasons.append("CF赤字⚠")

    stab = max(0, min(15, stab))
    breakdown["stability"] = stab
    score += stab

    # ── 株主還元（10点）──
    ret = 0

    # 配当利回りスコア（3%以上を重視）
    if div_yield >= 0.05:    ret += 5; 
    elif div_yield >= 0.03:  ret += 4
    elif div_yield >= 0.015: ret += 2

    # 連続増配ボーナス（米国：10年以上、日本：3年以上）
    if market == "US":
        if consecutive_div >= 10:   ret += 4; reasons.append(f"連続増配{consecutive_div}年")
        elif consecutive_div >= 5:  ret += 2; reasons.append(f"増配{consecutive_div}年")
    else:
        if consecutive_div >= 5:    ret += 3; reasons.append(f"連続増配{consecutive_div}年")
        elif consecutive_div >= 3:  ret += 2; reasons.append(f"増配{consecutive_div}年")

    # PBR1倍割れは資本効率への経営意識が高い場合が多い（追加点）
    if pbr is not None and pbr < 1.0:
        ret += 1

    ret = max(0, min(10, ret))
    breakdown["shareholder_return"] = ret
    score += ret

    score = max(0, min(65, score))
    return {
        "score":           score,
        "breakdown":       breakdown,
        "comment":         "・".join(reasons) if reasons else "データ限定",
        "stability_score": stab,
        "div_yield":       div_yield,
        "pe":              pe,
        "pbr":             pbr,
    }


# ─────────────────────────────────────────
# 第2スコア：買いタイミング（ファンダ担当分）
# ─────────────────────────────────────────

def calc_timing_score_fundamental(fd: dict, market: str = "US") -> dict:
    """
    買いタイミングスコアのファンダ担当分（50点満点）
    「今この株は割安か・買い時か」を評価
    """
    if not fd or "error" in fd:
        return {"score": 25, "breakdown": {}, "comment": "データ不足"}  # 中立

    trailing_pe   = fd.get("trailing_pe")
    forward_pe    = fd.get("forward_pe")
    pbr           = fd.get("pbr")
    div_yield     = fd.get("div_yield") or 0
    price_pos_52w = fd.get("price_position_52w")
    ex_div_date   = fd.get("ex_dividend_date")

    avg_per = HISTORICAL_AVG_PER.get(market, 20.0)
    score   = 0
    breakdown = {}
    reasons = []

    # ── 52週レンジ内の割安感（20点）──
    range_score = 0
    if price_pos_52w is not None:
        if price_pos_52w < 0.20:
            range_score = 20; reasons.append(f"52週安値圏({price_pos_52w*100:.0f}%)")
        elif price_pos_52w < 0.35:
            range_score = 15; reasons.append(f"52週下位({price_pos_52w*100:.0f}%)")
        elif price_pos_52w < 0.50:
            range_score = 10
        elif price_pos_52w < 0.70:
            range_score = 6
        elif price_pos_52w < 0.85:
            range_score = 3
        else:
            range_score = 0; reasons.append(f"52週高値圏({price_pos_52w*100:.0f}%)")
    else:
        range_score = 10  # データなし→中立

    breakdown["price_position"] = range_score
    score += range_score

    # ── PER乖離（15点）──
    pe_score = 0
    pe = forward_pe or trailing_pe
    if pe is not None:
        discount = (avg_per - pe) / avg_per  # プラス=割安、マイナス=割高
        if discount > 0.30:    pe_score = 15; reasons.append("PER大幅割安")
        elif discount > 0.15:  pe_score = 10; reasons.append("PER割安")
        elif discount > 0:     pe_score = 6
        elif discount > -0.20: pe_score = 3
        else:                  pe_score = 0;  reasons.append("PER割高")
    else:
        pe_score = 7  # データなし→中立

    breakdown["per_discount"] = pe_score
    score += pe_score

    # ── 配当タイミング（15点）──
    div_timing = 0
    if div_yield >= 0.03:
        # 配当落ち日が近い場合（1〜2ヶ月前）が最適タイミング
        if ex_div_date:
            from datetime import datetime, timezone
            try:
                now = datetime.now(timezone.utc)
                if hasattr(ex_div_date, 'timestamp'):
                    days_to_ex = (ex_div_date - now).days
                else:
                    days_to_ex = 999
                if 20 <= days_to_ex <= 60:
                    div_timing = 15; reasons.append(f"配当落ち{days_to_ex}日前(買い時)")
                elif 60 < days_to_ex <= 90:
                    div_timing = 10; reasons.append(f"配当落ち{days_to_ex}日前")
                elif 0 <= days_to_ex < 20:
                    div_timing = 5;  reasons.append(f"配当落ち直前({days_to_ex}日)")
                elif days_to_ex < 0:
                    div_timing = 3   # 配当落ち後
                else:
                    div_timing = 7   # 遠い
            except Exception:
                div_timing = 7
        else:
            # 配当落ち日不明だが高配当 → 中立点
            div_timing = 8 if div_yield >= 0.03 else 5
    else:
        div_timing = 5  # 非配当株は中立

    breakdown["dividend_timing"] = div_timing
    score += div_timing

    score = max(0, min(50, score))
    return {
        "score":     score,
        "breakdown": breakdown,
        "comment":   "・".join(reasons) if reasons else "",
    }


# ─────────────────────────────────────────
# バーゲン検知（市場急落時の割安判定）
# ─────────────────────────────────────────

def detect_bargain(fd: dict, quality_result: dict, macro: dict, market: str = "US") -> dict:
    vix     = macro.get("vix", 20)
    sp500   = macro.get("sp500", {})
    nk225   = macro.get("nk225", {})
    ret_30d = sp500.get("ret_30d", 0) if market == "US" else nk225.get("ret_30d", 0)

    pe            = fd.get("trailing_pe") or fd.get("forward_pe")
    pbr           = fd.get("pbr")
    price_pos_52w = fd.get("price_position_52w")
    stability     = quality_result.get("stability_score", 0)
    avg_per       = HISTORICAL_AVG_PER.get(market, 20.0)

    bargain_score = 0
    reasons       = []

    market_fearful = vix >= 25
    if vix >= 35:   bargain_score += 20; reasons.append(f"VIX急騰({vix:.0f})=恐怖感MAX")
    elif vix >= 25: bargain_score += 10; reasons.append(f"VIX高({vix:.0f})=市場警戒")

    market_down = ret_30d <= -0.08
    if ret_30d <= -0.15:   bargain_score += 20; reasons.append(f"市場急落({ret_30d*100:.0f}%)")
    elif ret_30d <= -0.08: bargain_score += 10; reasons.append(f"市場下落({ret_30d*100:.0f}%)")

    pe_cheap = False
    if pe is not None:
        if pe < avg_per * 0.70:   pe_cheap = True; bargain_score += 20; reasons.append(f"PER大割安({pe:.1f})")
        elif pe < avg_per * 0.85: pe_cheap = True; bargain_score += 10; reasons.append(f"PER割安({pe:.1f})")

    if pbr is not None and pbr < 1.2:
        bargain_score += 10; reasons.append(f"PBR低({pbr:.1f})")

    price_cheap = False
    if price_pos_52w is not None:
        if price_pos_52w < 0.25:   price_cheap = True; bargain_score += 15; reasons.append(f"52週安値圏({price_pos_52w*100:.0f}%)")
        elif price_pos_52w < 0.40: price_cheap = True; bargain_score += 8;  reasons.append(f"52週下位({price_pos_52w*100:.0f}%)")

    is_bargain = (
        (market_fearful or market_down)
        and (pe_cheap or price_cheap)
        and stability >= 5
        and bargain_score >= 30
    )

    return {
        "is_bargain":    is_bargain,
        "bargain_score": min(bargain_score, 100),
        "reasons":       reasons,
    }


# ─────────────────────────────────────────
# 後方互換（scoring_engineから呼ばれる旧関数名）
# ─────────────────────────────────────────

def calc_fundamental_score(fd: dict, market: str = "US") -> dict:
    """後方互換ラッパー：quality_scoreを返す"""
    return calc_quality_score(fd, market)


# ─────────────────────────────────────────
# 追加データ取得（yfinance拡張）
# ─────────────────────────────────────────

def get_extended_fundamental(ticker: str) -> dict:
    """
    連続増配年数・営業CF・粗利益率・配当落ち日など
    基本のget_fundamental_dataに追加するデータを取得
    """
    try:
        import yfinance as yf
        t    = yf.Ticker(ticker)
        info = t.info

        # 配当落ち日
        ex_div = info.get("exDividendDate")
        if ex_div:
            from datetime import datetime, timezone
            try:
                ex_div = datetime.fromtimestamp(ex_div, tz=timezone.utc)
            except Exception:
                ex_div = None

        # 連続増配年数の近似（5年分の配当履歴から計算）
        consecutive = 0
        try:
            hist_div = t.dividends
            if not hist_div.empty and len(hist_div) >= 2:
                # 年別集計
                import pandas as pd
                yearly = hist_div.resample("YE").sum()
                yearly = yearly[yearly > 0]
                if len(yearly) >= 2:
                    increases = 0
                    for i in range(1, len(yearly)):
                        if yearly.iloc[i] >= yearly.iloc[i-1] * 0.98:  # 2%以内の変動は維持扱い
                            increases += 1
                        else:
                            break
                    consecutive = increases
        except Exception:
            consecutive = 0

        return {
            "ex_dividend_date":            ex_div,
            "consecutive_dividend_years":  consecutive,
            "gross_margins":               info.get("grossMargins"),
            "operating_cashflow":          info.get("operatingCashflow"),
            "total_revenue":               info.get("totalRevenue"),
        }
    except Exception as e:
        logger.debug(f"拡張データ取得失敗 {ticker}: {e}")
        return {
            "ex_dividend_date":           None,
            "consecutive_dividend_years": 0,
            "gross_margins":              None,
            "operating_cashflow":         None,
            "total_revenue":              None,
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys, os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pipeline.market_data import get_fundamental_data, get_all_macro_data

    macro = get_all_macro_data()

    for ticker, market in [("KO", "US"), ("JNJ", "US"), ("7203.T", "JP")]:
        print(f"\n=== {ticker} ===")
        fd  = get_fundamental_data(ticker)
        ext = get_extended_fundamental(ticker)
        fd.update(ext)

        quality = calc_quality_score(fd, market)
        timing  = calc_timing_score_fundamental(fd, market)
        bargain = detect_bargain(fd, quality, macro, market)

        print(f"品質スコア:   {quality['score']}/65  {quality['breakdown']}")
        print(f"タイミング:   {timing['score']}/50  {timing['breakdown']}")
        print(f"バーゲン:     {bargain['is_bargain']}  スコア:{bargain['bargain_score']}")
        print(f"コメント:     {quality['comment']}")
        print(f"タイミング理由: {timing['comment']}")
        if ext['ex_dividend_date']:
            print(f"配当落ち日:   {ext['ex_dividend_date'].strftime('%Y-%m-%d')}")
        print(f"連続増配:     {ext['consecutive_dividend_years']}年")