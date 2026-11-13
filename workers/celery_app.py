import os

from celery import Celery

broker_url = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")

app = Celery(
    "hiveflows",
    broker=broker_url,
    backend=result_backend,
    include=["tasks.run_python", "tasks.run_bash", "tasks.run_translate", "tasks.run_fabric", "tasks.run_convert"],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
)
