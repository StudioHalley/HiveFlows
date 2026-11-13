from celery import Celery

from config import settings

celery_app = Celery(
    "hiveflows",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
