from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.db import engine
from app.db.models import APIJob
from app.feedback.service import FeedbackAnalysisService
from app.observability import EventLog
from app.public_api.jobs import create_internal_job, register_job_handler


def enqueue_feedback_analysis(tenant_id: str, feedback_id: str, session_id: str | None = None) -> APIJob:
    """创建一条 feedback.analyze 内部任务，把反馈分析投递到后台任务队列。

    请求载荷带上 tenant_id、feedback_id 与 session_id，返回已落库的 APIJob。
    """
    with Session(engine) as db:
        return create_internal_job(
            db,
            tenant_id=tenant_id,
            kind="feedback.analyze",
            request_payload={
                "tenant_id": tenant_id,
                "feedback_id": feedback_id,
                "session_id": session_id,
            },
        )


def run_feedback_analysis_job(payload: dict[str, Any]) -> None:
    """在独立数据库会话中执行反馈分析，并按结果记录完成或失败事件。

    反馈行不存在时，仅在租户与会话 id 都齐全时记录 feedback_analysis_error。
    """
    tenant_id = str(payload.get("tenant_id") or "")
    feedback_id = str(payload.get("feedback_id") or "")
    session_id = str(payload.get("session_id") or "")
    with Session(engine) as db:
        events = EventLog(db)
        row = FeedbackAnalysisService(db).analyze_feedback(feedback_id)
        if row:
            events.record(
                row.tenant_id,
                row.session_id,
                "feedback_analysis_completed",
                {
                    "feedback_id": row.id,
                    "message_id": row.message_id,
                    "rating": row.rating,
                    "bucket": row.analysis_bucket,
                    "status": row.analysis_status,
                    "confidence": row.analysis_confidence,
                },
            )
            db.commit()
            return
        if tenant_id and session_id:
            events.record(
                tenant_id,
                session_id,
                "feedback_analysis_error",
                {"feedback_id": feedback_id, "message": "Feedback row not found"},
            )
            db.commit()


@register_job_handler("feedback.analyze")
def handle_feedback_analysis_job(db: Session, job: APIJob) -> dict[str, Any]:
    """作为 feedback.analyze 的任务处理器执行分析，并返回结果摘要。

    反馈行缺失或归属租户与任务不一致时先记录失败事件，再抛 LookupError 让任务失败。
    """
    payload = dict(job.request_json or {})
    tenant_id = str(payload.get("tenant_id") or job.tenant_id)
    feedback_id = str(payload.get("feedback_id") or "")
    session_id = str(payload.get("session_id") or "")
    events = EventLog(db)
    row = FeedbackAnalysisService(db).analyze_feedback(feedback_id)
    if not row or row.tenant_id != tenant_id:
        if tenant_id and session_id:
            events.record(
                tenant_id,
                session_id,
                "feedback_analysis_error",
                {"feedback_id": feedback_id, "message": "Feedback row not found"},
            )
        raise LookupError("Feedback row not found")
    events.record(
        row.tenant_id,
        row.session_id,
        "feedback_analysis_completed",
        {
            "feedback_id": row.id,
            "message_id": row.message_id,
            "rating": row.rating,
            "bucket": row.analysis_bucket,
            "status": row.analysis_status,
            "confidence": row.analysis_confidence,
        },
    )
    return {
        "feedback_id": row.id,
        "analysis_status": row.analysis_status,
        "analysis_bucket": row.analysis_bucket,
    }
