"""
market_data.py
株価・財務・マクロデータ取得モジュール（yfinance使用・完全無料）
"""

import yfinance as yf
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# マクロデータ取得
# ─────────────────────────────────────────

def get_vix() -> float:
    """VIX（恐怖指数）の現在値を取得"""
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="2d")
        if hist.empty:
            logger.warning("VIXデータ取得失敗、デフォルト値20を使用")
            return 20.0
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.error(f"VIX取得エラー: {e}")
        return 20.0


def get_market_trend(ticker: str = "^GSPC", days: int = 30) -> dict:
    """
    市場全体のトレンドを取得
    - 直近30日リターン
    - 5日/20日移動平均クロス判定
    - 52週高値からの下落率（drawdown）
    """
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="6mo")
        if hist.empty:
            return _empty_trend()

        close = hist["Close"]

        # 直近30日リターン
        ret_30d = (close.iloc[-1] - close.iloc[-min(days, len(close))]) / close.iloc[-min(days, len(close))]

        # 移動平均
        ma5       = close.rolling(5).mean().iloc[-1]
        ma20      = close.rolling(20).mean().iloc[-1]
        ma5_prev  = close.rolling(5).mean().iloc[-2]
        ma20_prev = close.rolling(20).mean().iloc[-2]

        golden_cross = (ma5_prev < ma20_prev) and (ma5 > ma20)
        dead_cross   = (ma5_prev > ma20_prev) and (ma5 < ma20)
        trend_up     = ma5 > ma20

        # 52週高値からの下落率
        high_52w = close.tail(252).max()
        drawdown = (close.iloc[-1] - high_52w) / high_52w  # 負の値

        return {
            "ticker": ticker,
            "current_price": float(close.iloc[-1]),
            "ret_30d": float(ret_30d),
            "ma5": float(ma5),
            "ma20": float(ma20),
            "trend_up": bool(trend_up),
            "golden_cross": bool(golden_cross),
            "dead_cross": bool(dead_cross),
            "drawdown_52w": float(drawdown),
        }
    except Exception as e:
        logger.error(f"市場トレンド取得エラー ({ticker}): {e}")
        return _empty_trend()


def _empty_trend() -> dict:
    return {
        "ticker": "", "current_price": 0, "ret_30d": 0,
        "ma5": 0, "ma20": 0, "trend_up": True,
        "golden_cross": False, "dead_cross": False, "drawdown_52w": 0,
    }


def get_all_macro_data() -> dict:
    """マクロ判定に必要なデータを一括取得"""
    logger.info("マクロデータ取得開始...")
    vix    = get_vix()
    sp500  = get_market_trend("^GSPC")
    nk225  = get_market_trend("^N225")
    usdjpy = _get_fx("USDJPY=X")

    logger.info(f"  VIX: {vix:.1f}  |  SP500 30d: {sp500['ret_30d']*100:.1f}%  |  NK225 30d: {nk225['ret_30d']*100:.1f}%")
    return {
        "vix": vix,
        "sp500": sp500,
        "nk225": nk225,
        "usdjpy": usdjpy,
        "fetched_at": datetime.now().isoformat(),
    }


def _get_fx(ticker: str) -> dict:
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if hist.empty:
            return {"rate": 150.0, "change_1d": 0.0}
        rate      = float(hist["Close"].iloc[-1])
        change_1d = float(hist["Close"].iloc[-1] - hist["Close"].iloc[-2]) if len(hist) >= 2 else 0.0
        return {"rate": rate, "change_1d": change_1d}
    except Exception:
        return {"rate": 150.0, "change_1d": 0.0}


# ─────────────────────────────────────────
# 個別銘柄データ取得
# ─────────────────────────────────────────

def _safe_float(value, min_val=None, max_val=None) -> Optional[float]:
    """
    異常値フィルター
    - None / NaN はそのまま None を返す
    - min_val / max_val の範囲外は None を返す
    """
    if value is None:
        return None
    try:
        v = float(value)
        if np.isnan(v) or np.isinf(v):
            return None
        if min_val is not None and v < min_val:
            return None
        if max_val is not None and v > max_val:
            return None
        return v
    except (TypeError, ValueError):
        return None


def get_stock_data(ticker: str, period: str = "1y") -> Optional[pd.DataFrame]:
    """株価履歴を取得（テクニカル計算用）"""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=period)
        if hist.empty or len(hist) < 20:
            return None
        return hist
    except Exception as e:
        logger.debug(f"株価履歴取得失敗 {ticker}: {e}")
        return None


def get_fundamental_data(ticker: str) -> dict:
    """
    財務データを取得
    異常値フィルター付き：yfinanceは稀に桁外れな値を返すため範囲チェックを行う
    """
    try:
        t    = yf.Ticker(ticker)
        info = t.info

        # ── 各指標を安全に取得（範囲外は None に変換）──
        trailing_pe     = _safe_float(info.get("trailingPE"),     min_val=0,    max_val=500)
        forward_pe      = _safe_float(info.get("forwardPE"),      min_val=0,    max_val=500)
        pbr             = _safe_float(info.get("priceToBook"),     min_val=0,    max_val=300)
        roe             = _safe_float(info.get("returnOnEquity"),  min_val=-5,   max_val=5)
        # 配当利回り：yfinanceは小数（0.005 = 0.5%）で返すが、稀に%単位で返す場合がある
        raw_div = info.get("dividendYield", 0) or 0
        div_yield = _safe_float(raw_div, min_val=0, max_val=0.30)  # 30%超は異常値として除外
        if div_yield is None:
            # %単位で入っていた場合（例：35.0 → 0.35 と解釈して再チェック）
            converted = _safe_float(raw_div / 100, min_val=0, max_val=0.30)
            div_yield = converted if converted is not None else 0.0

        revenue_growth  = _safe_float(info.get("revenueGrowth"),  min_val=-1,   max_val=10)
        earnings_growth = _safe_float(info.get("earningsGrowth"), min_val=-5,   max_val=20)
        debt_to_equity  = _safe_float(info.get("debtToEquity"),   min_val=0,    max_val=2000)
        current_ratio   = _safe_float(info.get("currentRatio"),   min_val=0,    max_val=50)
        market_cap      = info.get("marketCap", None)
        sector          = info.get("sector", "")
        beta            = _safe_float(info.get("beta"),            min_val=-3,   max_val=5) or 1.0

        week52_high   = _safe_float(info.get("fiftyTwoWeekHigh"),  min_val=0)
        week52_low    = _safe_float(info.get("fiftyTwoWeekLow"),   min_val=0)
        current_price = _safe_float(
            info.get("currentPrice") or info.get("regularMarketPrice"),
            min_val=0
        )

        # 52週レンジ内の現在位置（0=安値圏 / 1=高値圏）
        price_position_52w = None
        if week52_high and week52_low and current_price and (week52_high - week52_low) > 0:
            price_position_52w = (current_price - week52_low) / (week52_high - week52_low)

        return {
            "ticker":            ticker,
            "current_price":     current_price,
            "market_cap":        market_cap,
            "sector":            sector,
            "trailing_pe":       trailing_pe,
            "forward_pe":        forward_pe,
            "pbr":               pbr,
            "roe":               roe,
            "div_yield":         div_yield,
            "revenue_growth":    revenue_growth,
            "earnings_growth":   earnings_growth,
            "debt_to_equity":    debt_to_equity,
            "current_ratio":     current_ratio,
            "beta":              beta,
            "week52_high":       week52_high,
            "week52_low":        week52_low,
            "price_position_52w": price_position_52w,
        }
    except Exception as e:
        logger.debug(f"財務データ取得失敗 {ticker}: {e}")
        return {"ticker": ticker, "error": str(e)}


def get_batch_fundamental(tickers: list, delay: float = 0.3) -> list:
    """複数銘柄の財務データを一括取得"""
    import time
    results = []
    total = len(tickers)
    for i, ticker in enumerate(tickers):
        if i % 20 == 0:
            logger.info(f"  財務データ取得中... {i}/{total}")
        data = get_fundamental_data(ticker)
        results.append(data)
        time.sleep(delay)
    return results


# ─────────────────────────────────────────
# バーゲン判定に使う市場平均PER
# ─────────────────────────────────────────

def get_market_average_per() -> dict:
    """市場平均PERの参考値を取得"""
    try:
        spy = yf.Ticker("SPY").info
        sp500_per = _safe_float(spy.get("trailingPE"), min_val=5, max_val=100) or 22.0
    except Exception:
        sp500_per = 22.0

    return {
        "sp500_current_per":       sp500_per,
        "sp500_historical_avg_per": 22.0,
        "nk225_historical_avg_per": 15.0,
    }


# ─────────────────────────────────────────
# 動作確認用
# ─────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("=== マクロデータ ===")
    macro = get_all_macro_data()
    print(f"VIX: {macro['vix']:.1f}")
    print(f"S&P500 30日: {macro['sp500']['ret_30d']*100:.1f}%  52W drawdown: {macro['sp500']['drawdown_52w']*100:.1f}%")
    print(f"日経225 30日: {macro['nk225']['ret_30d']*100:.1f}%")
    print(f"ドル円: {macro['usdjpy']['rate']:.2f} ({macro['usdjpy']['change_1d']:+.2f})")

    print("\n=== 個別銘柄テスト（AAPL）===")
    f = get_fundamental_data("AAPL")
    print(f"株価:         ${f.get('current_price', 'N/A')}")
    print(f"PER:          {f.get('trailing_pe', 'N/A')}")
    print(f"PBR:          {f.get('pbr', 'N/A')}")
    print(f"配当利回り:   {(f.get('div_yield') or 0)*100:.2f}%")
    print(f"ROE:          {(f.get('roe') or 0)*100:.1f}%")
    print(f"売上成長率:   {(f.get('revenue_growth') or 0)*100:.1f}%")
    print(f"52週位置:     {f.get('price_position_52w', 'N/A')}")

    print("\n=== 日本株テスト（トヨタ）===")
    f2 = get_fundamental_data("7203.T")
    print(f"株価:         ¥{f2.get('current_price', 'N/A')}")
    print(f"PER:          {f2.get('trailing_pe', 'N/A')}")
    print(f"配当利回り:   {(f2.get('div_yield') or 0)*100:.2f}%")
    print(f"52週位置:     {f2.get('price_position_52w', 'N/A')}")

    print("\n=== 株価履歴テスト ===")
    hist = get_stock_data("AAPL", "3mo")
    if hist is not None:
        print(f"取得期間: {hist.index[0].date()} 〜 {hist.index[-1].date()}  ({len(hist)}営業日)")