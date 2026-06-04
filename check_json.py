import json, glob, os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "results")
files = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")), reverse=True)

if not files:
    print("JSONファイルが見つかりません")
    print("パス:", DATA_DIR)
else:
    print(f"JSONファイル: {files[0]}")
    with open(files[0], encoding="utf-8") as f:
        d = json.load(f)
    news = d.get("news", {})
    print("news keys:", list(news.keys()))
    print("article_details件数:", len(news.get("article_details", [])))
    print("acute_count:", news.get("acute_count", "キーなし"))
    print("crisis_count:", news.get("crisis_count", "キーなし"))

    # acute_countがない場合は古いフォーマット
    if "acute_count" not in news:
        print("\n→ 古いフォーマットのJSONです。python scheduler.py now で再生成してください。")
