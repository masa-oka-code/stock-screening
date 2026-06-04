"""スコア内訳デバッグ用"""
import logging, time, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.WARNING)

from pipeline.market_data import get_all_macro_data, get_fundamental_data, get_stock_data
from pipeline.technical import calc_technical_indicators, calc_technical_score
from pipeline.fundamental import calc_fundamental_score, detect_bargain
from pipeline.scoring_engine import calc_macro_score, load_config

config       = load_config()
macro        = get_all_macro_data()
macro_result = calc_macro_score(macro, config)

for ticker in ["AAPL", "JNJ", "KO", "7203.T", "4502.T"]:
    market = "JP" if ticker.endswith(".T") else "US"
    fd     = get_fundamental_data(ticker)
    fund   = calc_fundamental_score(fd, market)
    hist   = get_stock_data(ticker, "6mo")
    if hist is not None:
        ind  = calc_technical_indicators(hist)
        tech = calc_technical_score(ind)
    else:
        tech = {"score": 0, "comment": "なし"}

    macro_score   = macro_result["macro_score"]
    penalty       = abs(min(macro_score, 0)) / 200
    base          = fund["score"] + tech["score"] + 0 + 5   # news=0, sector=5
    total         = round(base * (1 - penalty), 1)

    print(f"\n{ticker}")
    print(f"  ファンダ:{fund['score']}/40  テクニカル:{tech['score']}/20  ニュース:0/30  セクター:5/10")
    print(f"  合計base:{base}  マクロペナルティ:{penalty*100:.0f}%  → 総合:{total}")
    print(f"  コメント: {fund['comment']}")
    time.sleep(0.3)
