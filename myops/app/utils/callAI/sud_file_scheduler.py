from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from app.utils.callAI import sud_file_creation as sud

#scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
scheduler = BackgroundScheduler(timezone="America/Chicago")

def run_sud_export():
    print(f"[SUD] Export started at {datetime.now()}")
    sud.main()
    print(f"[SUD] Export finished at {datetime.now()}")


def start_scheduler():
    scheduler.add_job(
        run_sud_export,
        trigger="cron",
        hour=0,
        minute=0
    )
    scheduler.start()
