"""
fix_csv.py
universe CSVのクォート問題を修正するスクリプト
"""
import os

files = [
    ("data/universe/universe_jp.csv", "shift-jis"),
    ("data/universe/universe_us.csv", "utf-8"),
]

for path, enc in files:
    if not os.path.exists(path):
        print(f"ファイルなし: {path}")
        continue

    with open(path, encoding=enc, errors="replace") as f:
        lines = [line.strip().strip('"') for line in f if line.strip()]

    with open(path, "w", encoding="utf-8", newline="") as f:
        for line in lines:
            f.write(line + "\n")

    print(f"修正完了: {path}")

# 確認
import pandas as pd
for path in ["data/universe/universe_jp.csv", "data/universe/universe_us.csv"]:
    df = pd.read_csv(path, encoding="utf-8")
    print(f"\n{path}")
    print("  列名:", df.columns.tolist())
    print("  行数:", len(df))
    print("  先頭:", df.iloc[0]["ticker"])
