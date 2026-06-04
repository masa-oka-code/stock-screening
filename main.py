"""
main.py - 株式スクリーニング Streamlit ダッシュボード
"""
import json, os, glob, sys
import streamlit as st
from datetime import datetime

st.set_page_config(page_title="株式スクリーニング", page_icon="📈", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=Noto+Sans+JP:wght@400;500;700&display=swap');
html, body, [class*="css"] { font-family: 'Noto Sans JP', sans-serif; }
.risk-LOW      { display:inline-block; padding:4px 14px; border-radius:20px; font-size:12px; font-weight:700; font-family:'IBM Plex Mono',monospace; background:#0d2e1a; color:#34d399; border:1px solid #34d399; }
.risk-MEDIUM   { display:inline-block; padding:4px 14px; border-radius:20px; font-size:12px; font-weight:700; font-family:'IBM Plex Mono',monospace; background:#2e1f0d; color:#fbbf24; border:1px solid #fbbf24; }
.risk-HIGH     { display:inline-block; padding:4px 14px; border-radius:20px; font-size:12px; font-weight:700; font-family:'IBM Plex Mono',monospace; background:#2e0d0d; color:#f87171; border:1px solid #f87171; }
.risk-CRITICAL { display:inline-block; padding:4px 14px; border-radius:20px; font-size:12px; font-weight:700; font-family:'IBM Plex Mono',monospace; background:#1f0d2e; color:#c084fc; border:1px solid #c084fc; }
.news-card     { padding:10px 14px; border-radius:8px; margin-bottom:8px; font-size:13px; border:1px solid #e2e8f0; }
.news-card.pos { border-left:4px solid #16a34a; background:#f0fdf4; }
.news-card.neg { border-left:4px solid #dc2626; background:#fef2f2; }
.news-card.neu { border-left:4px solid #94a3b8; background:#f8fafc; }
.news-title    { font-weight:600; color:#1e293b; line-height:1.4; margin-bottom:4px; }
.news-meta     { font-size:11px; color:#94a3b8; font-family:'IBM Plex Mono',monospace; }
.sector-row    { display:flex; justify-content:space-between; align-items:center; padding:8px 14px; border-radius:8px; margin-bottom:6px; font-size:13px; border:1px solid #e2e8f0; }
.sector-pos    { border-left:4px solid #16a34a; background:#f0fdf4; }
.sector-neg    { border-left:4px solid #dc2626; background:#fef2f2; }
.sector-neu    { border-left:4px solid #94a3b8; background:#f8fafc; }
.stock-ticker  { font-family:'IBM Plex Mono',monospace; font-size:22px; font-weight:600; color:#1e293b; }
.score-num     { font-family:'IBM Plex Mono',monospace; font-size:30px; font-weight:600; color:#3b82f6; }
.score-num.high    { color:#16a34a; }
.score-num.bargain { color:#d97706; }
.bd-chip { display:inline-block; background:#f1f5f9; border:1px solid #e2e8f0; border-radius:6px; padding:3px 10px; font-size:12px; color:#475569; font-family:'IBM Plex Mono',monospace; margin:2px; }
.reason-box         { background:#f8fafc; border-left:3px solid #3b82f6; border-radius:0 8px 8px 0; padding:10px 14px; margin-top:10px; font-size:13px; color:#374151; line-height:1.7; }
.reason-box.bargain { border-left-color:#d97706; background:#fffbf0; }
.bargain-tag { background:#fef3c7; border:1px solid #d97706; color:#92400e; border-radius:4px; padding:2px 8px; font-size:11px; font-weight:700; }
.kw-chip { display:inline-block; background:#fef2f2; border:1px solid #fca5a5; border-radius:6px; padding:3px 10px; font-size:12px; color:#dc2626; font-family:'IBM Plex Mono',monospace; margin:2px; font-weight:600; }
</style>
""", unsafe_allow_html=True)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "results")

@st.cache_data(ttl=300)
def load_latest_result():
    if not os.path.exists(DATA_DIR): return {}
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")), reverse=True)
    if not files: return {}
    with open(files[0], encoding="utf-8") as f: return json.load(f)

def load_result_by_date(date_str):
    path = os.path.join(DATA_DIR, date_str.replace("-","") + ".json")
    if not os.path.exists(path): return {}
    with open(path, encoding="utf-8") as f: return json.load(f)

def get_available_dates():
    if not os.path.exists(DATA_DIR): return []
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")), reverse=True)
    dates = []
    for f in files:
        name = os.path.basename(f).replace(".json","")
        try: dates.append(datetime.strptime(name, "%Y%m%d").strftime("%Y-%m-%d"))
        except: pass
    return dates

# ── サイドバー ──
with st.sidebar:
    st.markdown("### 📅 日付選択")
    dates = get_available_dates()
    if dates:
        selected_date = st.selectbox("表示する日付", dates, index=0)
        result = load_latest_result() if selected_date == dates[0] else load_result_by_date(selected_date)
    else:
        result = {}
        st.info("データがありません。\n\n`python scheduler.py now` を実行してください。")

    st.divider()
    st.markdown("### ⚙️ 表示・検索設定")

    market_filter     = st.multiselect("市場", ["US","JP"], default=["US","JP"])
    min_score         = st.slider("最低スコア", 0, 100, 55)
    max_candidates    = st.slider("最大表示銘柄数", 3, 20, 10)
    show_bargain_only = st.checkbox("バーゲン候補のみ", value=False)

    st.markdown("**高配当フィルター**")
    dividend_filter = st.checkbox("高配当銘柄のみ（配当2%以上）", value=False)
    if dividend_filter:
        min_div = st.slider("最低配当利回り (%)", 1.0, 8.0, 2.0, 0.5)
    else:
        min_div = 0.0

    st.markdown("**株価レンジ (USD)**")
    use_price_filter_us = st.checkbox("米国株 株価レンジ指定", value=False)
    if use_price_filter_us:
        price_range_us = st.slider("USD", 0, 2000, (0, 2000), 10)
    else:
        price_range_us = (0, 999999)

    st.markdown("**株価レンジ (JPY)**")
    use_price_filter_jp = st.checkbox("日本株 株価レンジ指定", value=False)
    if use_price_filter_jp:
        price_range_jp = st.slider("JPY", 0, 100000, (0, 100000), 500)
    else:
        price_range_jp = (0, 9999999)

    st.divider()
    st.divider()
    if st.button("今すぐ更新", use_container_width=True):
        with st.spinner("実行中..."):
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                from pipeline.scoring_engine import run_pipeline
                from notifier.email_sender import send_email
                import yaml
                config = yaml.safe_load(open("config.yaml", encoding="utf-8"))
                result = run_pipeline()
                send_email(config, result)
                st.cache_data.clear()
                st.success("完了・メール送信済み")
                st.rerun()
            except Exception as e:
                st.error(str(e))

if not result:
    st.info("データがありません。サイドバーの「今すぐ更新」を押してください。")
    st.stop()

macro        = result.get("macro", {})
candidates   = result.get("candidates", [])
bargains     = result.get("bargain_picks", [])
news         = result.get("news", {})
date_str     = result.get("date", "")
gen_time     = result.get("generated_at", "")[:16]
risk         = macro.get("risk_level", "LOW")
bargain_mode = macro.get("bargain_mode", False)

st.markdown(f"## 📈 株式スクリーニング　`{date_str}`")
col_i1, col_i2 = st.columns([3,1])
with col_i1:
    st.caption(f"生成: {gen_time}　候補: {len(candidates)}銘柄" + (f"　🔔 バーゲン: {len(bargains)}銘柄" if bargains else ""))
with col_i2:
    st.markdown(f'<span class="risk-{risk}">{risk}</span>' + ("&nbsp; 🔔 **バーゲンモード**" if bargain_mode else ""), unsafe_allow_html=True)

st.divider()

# ════════ 1. マクロ環境 ════════
st.markdown("### 1️⃣ マクロ環境")
vix, sp500_r, nk225_r, usdjpy = macro.get("vix",0), macro.get("sp500_30d",0), macro.get("nk225_30d",0), macro.get("usdjpy",0)
c1,c2,c3,c4 = st.columns(4)
c1.metric("VIX", f"{vix:.1f}", help="25超=警戒 / 35超=危険水準")
c2.metric("S&P500（30日）",  f"{sp500_r*100:+.1f}%")
c3.metric("日経225（30日）", f"{nk225_r*100:+.1f}%")
c4.metric("ドル円", f"¥{usdjpy:.1f}")
reasons = macro.get("reasons", [])
if reasons:
    st.warning("⚠️ " + " / ".join(reasons))
else:
    st.success("✅ マクロ環境に特段のリスクなし")

st.divider()

# ════════ 2. ニュース一覧 ════════
st.markdown("### 2️⃣ 本日のニュース")
article_details = news.get("article_details", [])
art_cnt         = news.get("article_count", 0)
acute_count     = news.get("acute_count", 0)
acute_keywords  = news.get("acute_keywords", [])
st.caption(f"解析件数: {art_cnt}件　急性危機KW: {acute_count}件")

if acute_count > 0:
    kw_chips = "".join([f'<span class="kw-chip">⚠️ {kw}</span>' for kw in acute_keywords])
    st.markdown(
        f'<div style="background:#fef2f2; border:1px solid #fca5a5; border-radius:8px; padding:12px 16px; margin-bottom:8px;">'
        f'<strong style="color:#dc2626;">⚠️ 急性危機キーワード検出（マクロペナルティ適用中）</strong><br>'
        f'<div style="margin-top:8px;">{kw_chips}</div>'
        f'</div>',
        unsafe_allow_html=True
    )

if article_details:
    pos_articles = sorted([a for a in article_details if a.get("sentiment",0) >  0.05], key=lambda x: -x.get("sentiment",0))
    neg_articles = sorted([a for a in article_details if a.get("sentiment",0) < -0.05], key=lambda x:  x.get("sentiment",0))
    tab_pos, tab_neg, tab_all = st.tabs([f"🟢 ポジティブ（{len(pos_articles)}件）", f"🔴 ネガティブ（{len(neg_articles)}件）", "📋 全件"])

    def render_news(a, cls):
        raw_title = a.get("title", "（タイトルなし）")
        title     = a.get("title_ja") or raw_title  # パイプラインで翻訳済み
        title     = title[:100]
        source    = a.get("source", "")
        sent      = a.get("sentiment", 0)
        sectors   = ", ".join(a.get("sectors", []))
        tickers   = ", ".join(a.get("tickers", []))
        ticker_str = f"&nbsp;|&nbsp; 関連: {tickers}" if tickers else ""
        # 翻訳済みの場合は原文を小さく表示
        orig_str = ""
        if title != raw_title:
            orig_str = f'<div style="font-size:10px;color:#94a3b8;margin-top:2px;">{raw_title[:80]}</div>'
        st.markdown(f"""
<div class="news-card {cls}">
<div class="news-title">{title}</div>
{orig_str}
<div class="news-meta">📰 {source} &nbsp;|&nbsp; 感情: {sent:+.2f} &nbsp;|&nbsp; {sectors}{ticker_str}</div>
</div>""", unsafe_allow_html=True)

    with tab_pos:
        if pos_articles:
            for a in pos_articles[:15]: render_news(a, "pos")
        else: st.info("ポジティブ記事なし")
    with tab_neg:
        if neg_articles:
            for a in neg_articles[:15]: render_news(a, "neg")
        else: st.info("ネガティブ記事なし")
    with tab_all:
        for a in sorted(article_details, key=lambda x: -abs(x.get("sentiment",0)))[:30]:
            s   = a.get("sentiment",0)
            cls = "pos" if s > 0.05 else ("neg" if s < -0.05 else "neu")
            render_news(a, cls)
else:
    st.info("ニュース記事データがありません。`python scheduler.py now` を再実行してください。")

st.divider()

# ════════ 3. セクター動向 ════════
st.markdown("### 3️⃣ セクター動向")
sector_scores = news.get("sector_scores", {})
if sector_scores:
    col_s1, col_s2 = st.columns(2)
    items = [(s,v) for s,v in sorted(sector_scores.items(), key=lambda x:-x[1]) if s != "General"]
    half  = (len(items)+1)//2
    for col, chunk in [(col_s1, items[:half]), (col_s2, items[half:])]:
        with col:
            for sector, score in chunk:
                if score > 17:   cls, icon, color = "sector-pos","▲","#16a34a"
                elif score < 13: cls, icon, color = "sector-neg","▼","#dc2626"
                else:            cls, icon, color = "sector-neu","─","#64748b"
                bw = int(score/30*100)
                st.markdown(f"""
<div class="sector-row {cls}">
<span style="color:{color}; font-weight:600;">{icon} {sector}</span>
<div style="display:flex; align-items:center; gap:10px;">
<div style="width:80px; background:#e2e8f0; border-radius:4px; height:6px;">
<div style="width:{bw}%; height:100%; border-radius:4px; background:{color};"></div></div>
<span style="font-family:'IBM Plex Mono',monospace; font-size:12px; color:{color}; font-weight:600;">{score:.0f}/30</span>
</div></div>""", unsafe_allow_html=True)
else:
    st.info("セクターデータがありません")

st.divider()

# ════════ 4. 候補銘柄 ════════
def price_ok(c):
    mkt   = c.get("market","")
    price = c.get("current_price", 0)
    if mkt == "US": return price_range_us[0] <= price <= price_range_us[1]
    if mkt == "JP": return price_range_jp[0] <= price <= price_range_jp[1]
    return True

filtered = [c for c in candidates
            if c.get("market") in market_filter
            and c.get("total_score",0) >= min_score
            and (not show_bargain_only or c.get("is_bargain"))
            and (not dividend_filter or c.get("div_yield",0) >= min_div/100)
            and price_ok(c)]

bargain_list = [c for c in filtered if c.get("is_bargain")]
normal_list  = [c for c in filtered if not c.get("is_bargain")][:max_candidates]
total_show   = len(bargain_list) + len(normal_list)
st.markdown(f"### 4️⃣ 候補銘柄（{total_show}銘柄）")

if not filtered:
    if macro.get("suspend"): st.error("⛔ マクロリスクが高いためスクリーニングを停止しました。")
    else: st.info("🔍 現在の条件を満たす候補銘柄はありません。フィルターを緩めてみてください。")

def build_reason(c):
    import re
    parts   = []
    bd      = c.get("breakdown",{})
    pe      = c.get("pe")
    div     = c.get("div_yield",0)
    fund    = bd.get("fundamental",0)
    tech    = bd.get("technical",0)
    ns      = bd.get("news",0)
    comment = c.get("comment","")
    if pe and pe < 12:   parts.append(f"PER{pe:.1f}倍と市場平均を大幅に下回る割安水準")
    elif pe and pe < 18: parts.append(f"PER{pe:.1f}倍と割安水準")
    elif pe and pe > 30: parts.append(f"PER{pe:.1f}倍とやや割高だが高成長で正当化できる水準")
    if div >= 0.04:      parts.append(f"配当利回り{div*100:.1f}%の高配当銘柄")
    elif div >= 0.02:    parts.append(f"配当利回り{div*100:.1f}%")
    for label, pct in re.findall(r'(売上|利益)\+(\d+)%', comment):
        parts.append(f"{label}成長率+{pct}%と高成長継続")
    if "ROE高" in comment: parts.append("ROEが高く資本効率が優秀")
    if fund >= 30:          parts.append("総合的な財務健全性が非常に高い")
    elif fund >= 20:        parts.append("財務・バリュエーションが良好")
    if tech >= 14:          parts.append("テクニカル面でも上昇トレンドが継続")
    elif tech >= 8:         parts.append("テクニカル面は安定的")
    if ns >= 20:            parts.append("関連ニュースがポジティブで追い風")
    elif ns <= 10:          parts.append("ニュース面はやや逆風だが他要因で補完")
    if c.get("is_bargain"): parts.append("市場全体の下落に引きずられた割安状態で中長期回復が期待できる")
    return "。".join(parts) + "。" if parts else comment

def render_card(c):
    score      = c.get("total_score", 0)
    q_score    = c.get("quality_score", 0)
    t_score    = c.get("timing_score", 0)
    judgment   = c.get("judgment", "候補")
    bd         = c.get("breakdown", {})
    q_bd       = bd.get("quality", {})
    t_bd       = bd.get("timing", {})
    is_bargain = c.get("is_bargain", False)
    mkt        = c.get("market", "")
    price      = c.get("current_price", 0)
    price_str  = f"¥{price:,.0f}" if mkt == "JP" else f"${price:,.2f}"
    pe_val     = c.get("pe")
    pe_str     = f"{pe_val:.1f}" if pe_val else "N/A"
    div_str    = f"{c.get('div_yield',0)*100:.1f}%"
    consec_div = c.get("consecutive_div", 0)
    ex_div     = c.get("ex_div_date", "")[:10] if c.get("ex_div_date") else ""

    # 判定ラベルの色
    judgment_color = {
        "BARGAIN": "#d97706", "積極推薦": "#16a34a",
        "候補": "#3b82f6", "要観察": "#f59e0b", "参考": "#94a3b8"
    }.get(judgment, "#94a3b8")
    bar_color = "#d97706" if is_bargain else ("#16a34a" if judgment == "積極推薦" else "#3b82f6")

    col_l, col_r = st.columns([4, 1])
    with col_l:
        badge = ' &nbsp;<span class="bargain-tag">🔔 BARGAIN</span>' if is_bargain else ""
        j_badge = f'<span style="background:{judgment_color}20; color:{judgment_color}; border:1px solid {judgment_color}; border-radius:4px; padding:2px 8px; font-size:11px; font-weight:700; margin-left:6px;">{judgment}</span>'
        st.markdown(f'<span class="stock-ticker">{c.get("ticker","")}</span>{badge}{j_badge}', unsafe_allow_html=True)
        st.caption(f'{c.get("name","")}　{c.get("sector","")}　{mkt}　{price_str}')
    with col_r:
        score_cls = "bargain" if is_bargain else ("high" if judgment == "積極推薦" else "")
        st.markdown(f'<div class="score-num {score_cls}" style="text-align:right;">{score:.0f}点</div>', unsafe_allow_html=True)

    # 2段階スコアバー
    st.markdown(f"""
<div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:8px 0 12px;">
  <div>
    <div style="font-size:11px; color:#64748b; margin-bottom:3px;">品質スコア（何を買うか）　{q_score:.0f}/100</div>
    <div style="background:#e2e8f0; border-radius:4px; height:8px;">
      <div style="width:{min(q_score,100)}%; height:100%; border-radius:4px; background:#3b82f6;"></div></div>
  </div>
  <div>
    <div style="font-size:11px; color:#64748b; margin-bottom:3px;">タイミングスコア（いつ買うか）　{t_score:.0f}/100</div>
    <div style="background:#e2e8f0; border-radius:4px; height:8px;">
      <div style="width:{min(t_score,100)}%; height:100%; border-radius:4px; background:{bar_color};"></div></div>
  </div>
</div>""", unsafe_allow_html=True)

    # 品質内訳
    chips_q = (
        f'<span class="bd-chip">ファンダ {q_bd.get("fundamental",0)}/65</span>'
        f'<span class="bd-chip">ニュース {q_bd.get("news",0):.0f}/15</span>'
        f'<span class="bd-chip">セクター {q_bd.get("sector",0):.0f}/20</span>'
        f'<span class="bd-chip">PER {pe_str}</span>'
        f'<span class="bd-chip">配当 {div_str}</span>'
    )
    if consec_div and consec_div > 0:
        chips_q += f'<span class="bd-chip" style="color:#16a34a;">連続増配{consec_div}年</span>'
    if ex_div:
        chips_q += f'<span class="bd-chip" style="color:#d97706;">配当落ち {ex_div}</span>'

    # タイミング内訳
    chips_t = (
        f'<span class="bd-chip">割安感 {t_bd.get("valuation",0)}/50</span>'
        f'<span class="bd-chip">マクロ {t_bd.get("macro",0)}/30</span>'
        f'<span class="bd-chip">テクニカル {t_bd.get("technical",0):.0f}/20</span>'
    )

    st.markdown(f"""
<div style="margin-bottom:4px; font-size:11px; color:#94a3b8;">▍品質</div>{chips_q}
<div style="margin:6px 0 4px; font-size:11px; color:#94a3b8;">▍タイミング</div>{chips_t}
""", unsafe_allow_html=True)

    reason     = build_reason(c)
    reason_cls = "bargain" if is_bargain else ""
    prefix     = "🔔 バーゲン推薦理由" if is_bargain else "📌 推薦理由"
    timing_comment = c.get("timing_comment", "")
    timing_html = f'<div style="margin-top:6px; font-size:12px; color:#64748b;">⏱ タイミング: {timing_comment}</div>' if timing_comment else ""
    st.markdown(f'<div class="reason-box {reason_cls}"><strong>{prefix}</strong><br>{reason}{timing_html}</div>', unsafe_allow_html=True)
    st.divider()

if bargain_list:
    st.markdown("#### 🔔 バーゲン買い候補（強推薦）")
    for c in bargain_list: render_card(c)
if normal_list:
    if bargain_list: st.markdown("#### 通常候補")
    for c in normal_list: render_card(c)

remainder = len([c for c in filtered if not c.get("is_bargain")]) - max_candidates
if remainder > 0:
    st.caption(f"※ 他{remainder}銘柄あり。サイドバーの「最大表示銘柄数」を増やすと表示されます。")

st.caption("このダッシュボードは自動生成です。投資判断はご自身の責任で行ってください。")