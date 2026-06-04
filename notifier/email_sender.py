"""
email_sender.py
Gmail SMTPでHTMLメールを送信するモジュール
"""

import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

logger = logging.getLogger(__name__)


def build_html(result: dict) -> str:
    """スコアリング結果からHTMLメール本文を生成"""

    macro      = result.get("macro", {})
    candidates = result.get("candidates", [])
    bargains   = result.get("bargain_picks", [])
    news       = result.get("news", {})
    date_str   = result.get("date", datetime.now().strftime("%Y-%m-%d"))

    risk       = macro.get("risk_level", "LOW")
    risk_color = {"LOW": "#27ae60", "MEDIUM": "#f39c12", "HIGH": "#e74c3c", "CRITICAL": "#8e44ad"}.get(risk, "#888")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; background:#f5f6fa; margin:0; padding:0; }}
  .wrap {{ max-width:680px; margin:0 auto; background:#fff; border-radius:8px; overflow:hidden; }}
  .header {{ background:#1a1a2e; color:#fff; padding:24px 32px; }}
  .header h1 {{ margin:0; font-size:22px; }}
  .header p {{ margin:4px 0 0; color:#aaa; font-size:13px; }}
  .section {{ padding:20px 32px; border-bottom:1px solid #eee; }}
  .section h2 {{ margin:0 0 12px; font-size:15px; color:#333; border-left:4px solid #3498db; padding-left:10px; }}
  .macro-badge {{ display:inline-block; padding:4px 12px; border-radius:20px; color:#fff; font-weight:bold; font-size:13px; background:{risk_color}; }}
  .macro-grid {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; margin-top:12px; }}
  .macro-item {{ background:#f8f9fa; border-radius:6px; padding:10px 14px; }}
  .macro-item .label {{ font-size:11px; color:#888; }}
  .macro-item .value {{ font-size:18px; font-weight:bold; color:#333; margin-top:2px; }}
  .card {{ border:1px solid #e0e0e0; border-radius:8px; padding:16px; margin-bottom:12px; }}
  .card.bargain {{ border-color:#e67e22; background:#fffbf5; }}
  .card-header {{ display:flex; justify-content:space-between; align-items:center; }}
  .ticker {{ font-size:18px; font-weight:bold; color:#1a1a2e; }}
  .score {{ font-size:22px; font-weight:bold; color:#3498db; }}
  .score.high {{ color:#27ae60; }}
  .score.bargain {{ color:#e67e22; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:bold; background:#e74c3c; color:#fff; margin-left:6px; }}
  .breakdown {{ display:flex; gap:8px; margin:8px 0; flex-wrap:wrap; }}
  .bd-item {{ background:#eaf0fb; border-radius:4px; padding:3px 8px; font-size:12px; color:#2c3e50; }}
  .comment {{ font-size:13px; color:#555; margin-top:6px; }}
  .sector-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }}
  .sector-item {{ padding:6px 12px; border-radius:4px; font-size:13px; }}
  .sector-pos {{ background:#eafaf1; color:#27ae60; }}
  .sector-neg {{ background:#fdecea; color:#e74c3c; }}
  .footer {{ padding:16px 32px; background:#f8f9fa; font-size:12px; color:#aaa; text-align:center; }}
</style></head><body>
<div class="wrap">
  <div class="header">
    <h1>stock 株式スクリーニング結果</h1>
    <p>{date_str} 引け後レポート</p>
  </div>
"""

    vix      = macro.get("vix", 0)
    sp500_r  = macro.get("sp500_30d", 0)
    nk225_r  = macro.get("nk225_30d", 0)
    usdjpy   = macro.get("usdjpy", 0)
    reasons  = " / ".join(macro.get("reasons", [])) or "特になし"
    bargain_mode = macro.get("bargain_mode", False)

    html += f"""
  <div class="section">
    <h2>マクロ環境</h2>
    <span class="macro-badge">{risk}</span>
    {"<span style='margin-left:8px;color:#e67e22;font-weight:bold;'>BARGAIN バーゲンモード</span>" if bargain_mode else ""}
    <div class="macro-grid">
      <div class="macro-item"><div class="label">VIX（恐怖指数）</div><div class="value">{vix:.1f}</div></div>
      <div class="macro-item"><div class="label">S&amp;P500（30日）</div><div class="value">{sp500_r*100:+.1f}%</div></div>
      <div class="macro-item"><div class="label">日経225（30日）</div><div class="value">{nk225_r*100:+.1f}%</div></div>
      <div class="macro-item"><div class="label">ドル円</div><div class="value">Y{usdjpy:.1f}</div></div>
      <div class="macro-item" style="grid-column:span 2"><div class="label">リスク要因</div><div style="font-size:13px;margin-top:4px;">{reasons}</div></div>
    </div>
  </div>
"""

    top_pos = news.get("top_positive", [])
    top_neg = news.get("top_negative", [])
    art_cnt = news.get("article_count", 0)
    acute   = news.get("acute_count", 0)

    html += f"""
  <div class="section">
    <h2>本日のニュース概況（{art_cnt}件解析）</h2>
    {"<p style='color:#e74c3c;font-weight:bold;'>急性危機キーワード検出: " + str(acute) + "件</p>" if acute > 0 else ""}
    <div class="sector-grid">
"""
    for s, score in top_pos[:3]:
        html += f'      <div class="sector-item sector-pos">up {s} ({score:.0f}点)</div>\n'
    for s, score in top_neg[:3]:
        html += f'      <div class="sector-item sector-neg">down {s} ({score:.0f}点)</div>\n'
    html += "    </div>\n"

    # 注目ニュース記事（title_jaがあれば日本語で表示）
    article_details = news.get("article_details", [])
    if article_details:
        top_articles = sorted(
            [a for a in article_details if abs(a.get("sentiment", 0)) > 0.1],
            key=lambda x: -abs(x.get("sentiment", 0))
        )[:6]
        if top_articles:
            html += "    <div style='margin-top:12px;'><strong style='font-size:13px;color:#333;'>注目ニュース</strong><br>"
            for a in top_articles:
                title = a.get("title_ja") or a.get("title", "")
                sent  = a.get("sentiment", 0)
                color = "#27ae60" if sent > 0 else "#e74c3c"
                icon  = "&#9650;" if sent > 0 else "&#9660;"
                html += f"<div style='padding:4px 0; font-size:12px; border-bottom:1px solid #f0f0f0;'><span style='color:{color};'>{icon}</span> {title[:70]}</div>"
            html += "</div>"

    html += "  </div>\n"

    if bargains:
        html += """
  <div class="section">
    <h2>BARGAIN バーゲン買い候補（強推薦）</h2>
"""
        for c in bargains:
            score     = c.get("total_score", 0)
            bd        = c.get("breakdown", {})
            b_reasons = "・".join(c.get("bargain_reasons", []))
            html += f"""
    <div class="card bargain">
      <div class="card-header">
        <div><span class="ticker">{c['ticker']}</span>
          <span style="color:#888;font-size:13px;"> {c.get('name','')}</span>
          <span class="badge">BARGAIN</span></div>
        <div class="score bargain">{score:.0f}点</div>
      </div>
      <div class="breakdown">
        <span class="bd-item">ファンダ {bd.get('fundamental',0)}/40</span>
        <span class="bd-item">テクニカル {bd.get('technical',0)}/20</span>
        <span class="bd-item">ニュース {bd.get('news',0)}/30</span>
        <span class="bd-item">配当 {c.get('div_yield',0)*100:.1f}%</span>
      </div>
      <div class="comment">{b_reasons}</div>
      <div class="comment" style="margin-top:4px;">{c.get('comment','')}</div>
    </div>
"""
        html += "  </div>\n"

    normal = [c for c in candidates if not c.get("is_bargain")]
    if normal:
        html += f"""
  <div class="section">
    <h2>本日の候補銘柄（{len(normal)}銘柄）</h2>
"""
        for c in normal:
            score     = c.get("total_score", 0)
            bd        = c.get("breakdown", {})
            score_cls = "high" if score >= 70 else ""
            pe_val    = c.get('pe')
            pe_str    = f"{pe_val:.1f}" if pe_val else "N/A"
            html += f"""
    <div class="card">
      <div class="card-header">
        <div><span class="ticker">{c['ticker']}</span>
          <span style="color:#888;font-size:13px;margin-left:6px;">{c.get('name','')}</span>
          <span style="color:#888;font-size:12px;margin-left:6px;">{c.get('sector','')}</span></div>
        <div class="score {score_cls}">{score:.0f}点</div>
      </div>
      <div class="breakdown">
        <span class="bd-item">ファンダ {bd.get('fundamental',0)}/40</span>
        <span class="bd-item">テクニカル {bd.get('technical',0)}/20</span>
        <span class="bd-item">ニュース {bd.get('news',0)}/30</span>
        <span class="bd-item">PER {pe_str}</span>
        <span class="bd-item">配当 {c.get('div_yield',0)*100:.1f}%</span>
      </div>
      <div class="comment">{c.get('comment','')}</div>
    </div>
"""
        html += "  </div>\n"

    if not candidates:
        suspend = macro.get("suspend", False)
        msg = "マクロリスクが高いため本日のスクリーニングを停止しました。" if suspend else "本日の条件を満たす候補銘柄はありませんでした。"
        html += f"""
  <div class="section">
    <h2>本日の候補銘柄</h2>
    <p style="color:#888;text-align:center;padding:20px;">{msg}</p>
  </div>
"""

    html += """
  <div class="footer">このメールは自動生成されています。投資判断はご自身の責任で行ってください。</div>
</div></body></html>"""

    return html


def send_email(config: dict, result: dict) -> bool:
    """HTMLメールを送信"""
    email_cfg    = config.get("email", {})
    sender       = email_cfg.get("sender", "")
    receiver     = email_cfg.get("receiver", "")
    app_password = email_cfg.get("app_password", "")

    if not all([sender, receiver, app_password]):
        logger.warning("メール設定が不完全です（config.yamlを確認してください）")
        return False

    date_str    = result.get("date", datetime.now().strftime("%Y-%m-%d"))
    candidate_n = result.get("candidate_count", 0)
    bargain_n   = len(result.get("bargain_picks", []))
    risk        = result.get("macro", {}).get("risk_level", "LOW")

    subject = f"【株式レポート {date_str}】候補{candidate_n}銘柄"
    if bargain_n > 0:
        subject += f" バーゲン{bargain_n}銘柄"
    if risk in ("HIGH", "CRITICAL"):
        subject += f" マクロ{risk}"

    html_body = build_html(result)

    # 件名をRFC2047エンコード（日本語対応）
    from email.header import Header
    html_safe = html_body.encode("ascii", "xmlcharrefreplace").decode("ascii")
    subject_encoded = Header(subject.encode("utf-8"), "utf-8").encode()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject_encoded
    msg["From"]    = sender
    msg["To"]      = receiver
    msg.attach(MIMEText(html_safe, "html", "us-ascii"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, app_password)
            server.sendmail(sender, receiver, msg.as_string())
        logger.info("メール送信成功: " + receiver)
        return True
    except Exception as e:
        logger.error("メール送信失敗: " + str(e))
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import sys, os, yaml
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    config = yaml.safe_load(open("config.yaml", encoding="utf-8"))

    dummy = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "macro": {
            "vix": 16.1, "sp500_30d": 0.067, "nk225_30d": 0.146,
            "usdjpy": 149.5, "risk_level": "LOW", "macro_score": 0,
            "bargain_mode": False, "suspend": False, "reasons": [],
        },
        "news": {
            "article_count": 126, "crisis_count": 7, "acute_count": 0,
            "top_positive": [("Technology", 18.2), ("Consumer", 16.5)],
            "top_negative": [("Healthcare", 11.2), ("Macro", 12.0)],
            "ticker_scores": {},
        },
        "candidates": [
            {
                "ticker": "AAPL", "name": "Apple Inc.", "sector": "Technology",
                "current_price": 312.0, "total_score": 60.0, "is_bargain": False,
                "bargain_score": 0, "bargain_reasons": [],
                "breakdown": {"fundamental": 20, "technical": 14, "news": 21, "sector": 5},
                "div_yield": 0.0035, "pe": 31.9,
                "comment": "PER割高・売上+17%・利益+22%・ROE高・中期↑",
            },
            {
                "ticker": "7203.T", "name": "トヨタ自動車", "sector": "輸送用機器",
                "current_price": 3042.0, "total_score": 58.0, "is_bargain": False,
                "bargain_score": 0, "bargain_reasons": [],
                "breakdown": {"fundamental": 26, "technical": 11, "news": 16, "sector": 5},
                "div_yield": 0.033, "pe": 9.0,
                "comment": "PER割安・PBR<1・配当3.3%・利益+23%",
            },
        ],
        "bargain_picks": [],
        "candidate_count": 2,
    }

    print("テストメール送信中...")
    ok = send_email(config, dummy)
    print("送信結果:", "成功" if ok else "失敗")