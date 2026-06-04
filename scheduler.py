"""
scheduler.py
APSchedulerで毎晩19時にパイプラインを自動実行してメール通知するスクリプト
実行方法: python scheduler.py
"""

import logging
import os
import sys
import yaml
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline.scoring_engine import run_pipeline, load_config
from notifier.email_sender import send_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scheduler.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)

# CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
# 
# 
# def load_config() -> dict:
#     with open(CONFIG_PATH, encoding="utf-8") as f:
#         return yaml.safe_load(f)


def run_daily_job():
    """毎日19時に実行されるメインジョブ"""
    logger.info("=" * 50)
    logger.info("定時ジョブ開始: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 50)

    config = load_config()

    try:
        # パイプライン実行
        result = run_pipeline()
        logger.info("パイプライン完了: 候補" + str(result.get("candidate_count", 0)) + "銘柄")

        # メール送信
        if config.get("email", {}).get("enabled", False):
            ok = send_email(config, result)
            logger.info("メール送信: " + ("成功" if ok else "失敗"))
        else:
            logger.info("メール送信: 無効（config.yaml で enabled: true にしてください）")

    except Exception as e:
        logger.error("ジョブ実行エラー: " + str(e))
        import traceback
        logger.error(traceback.format_exc())

    logger.info("定時ジョブ完了")


def run_now():
    """今すぐ手動実行（テスト用）"""
    logger.info("手動実行モード")
    run_daily_job()


if __name__ == "__main__":
    config     = load_config()
    send_hour  = config.get("email", {}).get("send_hour", 19)

    # 引数で "now" を渡すと即時実行
    if len(sys.argv) > 1 and sys.argv[1] == "now":
        run_now()
        sys.exit(0)

    logger.info("スケジューラー起動")
    logger.info("実行予定: 毎日 " + str(send_hour) + ":00")
    logger.info("停止するには Ctrl+C を押してください")

    scheduler = BlockingScheduler(timezone="Asia/Tokyo")
    scheduler.add_job(
        run_daily_job,
        trigger="cron",
        hour=send_hour,
        minute=0,
        id="daily_stock_job",
        name="株式スクリーニング定時実行",
    )

    # 起動時に次回実行時刻を表示
    job = scheduler.get_job("daily_stock_job")
    logger.info("次回実行予定: " + str(scheduler.get_jobs()[0].next_run_time if scheduler.get_jobs() else "未設定"))

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("スケジューラー停止")