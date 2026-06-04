"""
technical.py
テクニカル指標の計算とスコアリング（taライブラリ使用）
"""

import pandas as pd
import numpy as np
import logging
from typing import Optional
import ta

logger = logging.getLogger(__name__)


def calc_technical_indicators(hist: pd.DataFrame) -> dict:
    """
    株価履歴からテクニカル指標を計算
    hist: yfinanceのhistory()で取得したDataFrame
    """
    if hist is None or len(hist) < 20:
        return {}

    close  = hist["Close"]
    high   = hist["High"]
    low    = hist["Low"]
    volume = hist["Volume"]

    result = {}

    try:
        # ── トレンド系 ──
        result["ma5"]  = float(close.rolling(5).mean().iloc[-1])
        result["ma20"] = float(close.rolling(20).mean().iloc[-1])
        result["ma60"] = float(close.rolling(min(60, len(close))).mean().iloc[-1])

        result["trend_short"]  = result["ma5"]  > result["ma20"]   # 短期上昇トレンド
        result["trend_medium"] = result["ma20"] > result["ma60"]   # 中期上昇トレンド

        # ゴールデンクロス / デッドクロス（直近2日）
        ma5_s  = close.rolling(5).mean()
        ma20_s = close.rolling(20).mean()
        result["golden_cross"] = bool(ma5_s.iloc[-2] < ma20_s.iloc[-2] and ma5_s.iloc[-1] > ma20_s.iloc[-1])
        result["dead_cross"]   = bool(ma5_s.iloc[-2] > ma20_s.iloc[-2] and ma5_s.iloc[-1] < ma20_s.iloc[-1])

        # ── モメンタム系 ──
        rsi_indicator = ta.momentum.RSIIndicator(close=close, window=14)
        result["rsi"] = float(rsi_indicator.rsi().iloc[-1])

        macd_indicator = ta.trend.MACD(close=close)
        result["macd"]        = float(macd_indicator.macd().iloc[-1])
        result["macd_signal"] = float(macd_indicator.macd_signal().iloc[-1])
        result["macd_hist"]   = float(macd_indicator.macd_diff().iloc[-1])
        result["macd_bullish"] = result["macd_hist"] > 0  # MACDヒストグラムがプラス

        # ── ボラティリティ系 ──
        bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
        result["bb_upper"]  = float(bb.bollinger_hband().iloc[-1])
        result["bb_lower"]  = float(bb.bollinger_lband().iloc[-1])
        result["bb_middle"] = float(bb.bollinger_mavg().iloc[-1])
        current_price = float(close.iloc[-1])
        bb_width = result["bb_upper"] - result["bb_lower"]
        if bb_width > 0:
            result["bb_position"] = (current_price - result["bb_lower"]) / bb_width
        else:
            result["bb_position"] = 0.5  # 幅ゼロ時は中央

        # ── 出来高系 ──
        vol_ma20 = float(volume.rolling(20).mean().iloc[-1])
        vol_now  = float(volume.iloc[-1])
        result["volume_ratio"] = vol_now / vol_ma20 if vol_ma20 > 0 else 1.0  # 1.0超=出来高増加

        result["current_price"] = current_price

    except Exception as e:
        logger.debug(f"テクニカル指標計算エラー: {e}")

    return result


def calc_technical_score(indicators: dict) -> dict:
    """
    テクニカル指標からスコアを算出（20点満点）

    採点基準：
    ・トレンド（+8点）：中期上昇トレンド、ゴールデンクロスなど
    ・モメンタム（+8点）：RSI適正範囲、MACD強気
    ・ボラティリティ（+4点）：BB内の位置、過熱感チェック

    バーゲン時の補正：
    ・RSIが売られすぎ（30以下）でも、バーゲンモード時はペナルティなし
    """
    if not indicators:
        return {"score": 0, "breakdown": {}, "comment": "データ不足"}

    score = 0
    breakdown = {}
    reasons = []

    rsi        = indicators.get("rsi", 50)
    macd_bull  = indicators.get("macd_bullish", False)
    trend_s    = indicators.get("trend_short", False)
    trend_m    = indicators.get("trend_medium", False)
    golden     = indicators.get("golden_cross", False)
    dead       = indicators.get("dead_cross", False)
    bb_pos     = indicators.get("bb_position", 0.5)
    vol_ratio  = indicators.get("volume_ratio", 1.0)

    # ── トレンドスコア（最大8点）──
    t_score = 0
    if trend_m:
        t_score += 4
        reasons.append("中期↑")
    if trend_s:
        t_score += 2
        reasons.append("短期↑")
    if golden:
        t_score += 2
        reasons.append("GC")
    if dead:
        t_score -= 4
        reasons.append("DC⚠")
    t_score = max(0, min(8, t_score))
    breakdown["trend"] = t_score
    score += t_score

    # ── モメンタムスコア（最大8点）──
    m_score = 0
    if 40 <= rsi <= 70:       # 適正範囲：買われすぎでも売られすぎでもない
        m_score += 4
        reasons.append(f"RSI適正({rsi:.0f})")
    elif rsi < 30:             # 売られすぎ（通常はペナルティ、バーゲン時は後で補正）
        m_score += 1
        reasons.append(f"RSI過売({rsi:.0f})")
    elif rsi > 75:             # 買われすぎ
        m_score -= 2
        reasons.append(f"RSI過熱({rsi:.0f})")

    if macd_bull:
        m_score += 4
        reasons.append("MACD↑")
    m_score = max(0, min(8, m_score))
    breakdown["momentum"] = m_score
    score += m_score

    # ── ボラティリティスコア（最大4点）──
    v_score = 0
    if 0.2 <= bb_pos <= 0.6:   # BB下半分〜中央：上昇余地あり
        v_score += 3
        reasons.append("BB割安圏")
    elif bb_pos > 0.85:         # BB上端付近：過熱
        v_score -= 1
        reasons.append("BB過熱")

    if vol_ratio > 1.5:         # 出来高急増：注目度上昇
        v_score += 1
        reasons.append(f"出来高↑{vol_ratio:.1f}x")
    v_score = max(0, min(4, v_score))
    breakdown["volatility"] = v_score
    score += v_score

    score = max(0, min(20, score))
    comment = "・".join(reasons) if reasons else "シグナルなし"

    return {
        "score":     score,
        "breakdown": breakdown,
        "comment":   comment,
        "rsi":       rsi,
        "macd_bullish": macd_bull,
        "bb_position":  bb_pos,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys, os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pipeline.market_data import get_stock_data

    for ticker in ["AAPL", "7203.T"]:
        print(f"\n=== {ticker} ===")
        hist = get_stock_data(ticker, "6mo")
        if hist is not None:
            ind   = calc_technical_indicators(hist)
            score = calc_technical_score(ind)
            print(f"RSI: {ind.get('rsi', 'N/A'):.1f}  MACD強気: {ind.get('macd_bullish')}  BB位置: {ind.get('bb_position', 0):.2f}")
            print(f"テクニカルスコア: {score['score']}/20  内訳: {score['breakdown']}")
            print(f"コメント: {score['comment']}")
