from celery import shared_task

from nba.ingest.runs import JobAlreadyRunning
from predictions.service import refresh_model as run_refresh


@shared_task(name="predictions.refresh_model")
def refresh_model():
    try:
        return {"job": "refresh_model", "rows": run_refresh()}
    except JobAlreadyRunning:
        return {"job": "refresh_model", "skipped": True}


@shared_task(name="predictions.run_analysis")
def run_analysis(analysis_id: str):
    from predictions.analysis import run_analysis as run

    analysis = run(analysis_id)
    return {"analysis_id": str(analysis.id), "status": analysis.status}


@shared_task(name="predictions.recover_stuck_analyses")
def recover_stuck_analyses():
    from predictions.analysis import recover_stuck

    return {"failed": recover_stuck()}
