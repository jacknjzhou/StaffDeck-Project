from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import httpx
from fastapi import HTTPException
from sqlalchemy import inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.external_business_tasks import (
    ExternalTaskCallback,
    external_business_task_callback,
    get_external_business_task,
)
from app.db.models import (
    ChatSession,
    ExternalBusinessTask,
    ExternalBusinessTaskEvent,
    HarnessAgentLoopRecord,
    HarnessTaskFrameRecord,
    Tenant,
    Tool,
    User,
)
from app.core.task_frame_store import TaskFrameStore, planned_frame_from_record
from app.session.session_schema import TurnPlan
from app.tools.external_tasks import callback_token_hash, poll_due_external_tasks
from app.tools.tool_executor import ToolExecutor
from app.tools.tool_schema import ToolCall, ToolResult


class _Client:
    """模拟 httpx.Client，返回预置响应并记录请求头供断言。"""
    response_json: ClassVar[dict] = {"taskId": "provider-42", "status": "queued"}
    request_headers: ClassVar[dict[str, str]] = {}

    def __init__(self, **_kwargs):
        """忽略构造参数，保持与 httpx.Client 的调用方式一致。"""

    def __enter__(self):
        """返回自身以支持 with 语句。"""
        return self

    def __exit__(self, *_args):
        """不处理异常，正常退出上下文。"""
        return

    def request(self, method, url, **kwargs):
        """记录本次请求头并返回 202 的预置 JSON 响应。"""
        del method, url
        type(self).request_headers = kwargs.get("headers", {})
        return httpx.Response(
            202,
            json=type(self).response_json,
            request=httpx.Request("POST", "https://provider.test/tasks"),
        )


def test_detached_submission_persists_task_and_returns_query_guidance(monkeypatch) -> None:
    """验证 detached 提交只落库任务并返回状态查询指引，不调用供应商。"""
    monkeypatch.setattr(
        "app.tools.tool_executor.httpx.Client",
        lambda **_: (_ for _ in ()).throw(AssertionError("Provider must not run during submit")),
    )
    with _session() as db:
        user, tool = _seed(db)
        result = ToolExecutor(db).execute(
            "tenant_demo",
            ToolCall(name=tool.name, arguments={"order": "A-1"}),
            user_id=user.id,
        )

        task = db.exec(select(ExternalBusinessTask)).one()
        assert result.success is True
        assert result.data["accepted"] is True
        assert result.data["task_id"] == task.id
        assert f"/external-business-tasks/{task.id}?" in result.data["status_query"]["path"]
        assert "tenant_id=tenant_demo&tool_id=tool_detached" in result.data["status_query"]["path"]
        assert task.external_task_id is None
        assert task.status == "queued"
        assert "下单任务" not in result.data["status_query"]["guidance"]


def test_detached_submission_does_not_require_provider_task_id(monkeypatch) -> None:
    """验证 detached 提交无需供应商任务号即可登记任务。"""
    monkeypatch.setattr(
        "app.tools.tool_executor.httpx.Client",
        lambda **_: (_ for _ in ()).throw(AssertionError("Provider must not run during submit")),
    )
    with _session() as db:
        user, tool = _seed(db)
        result = ToolExecutor(db).execute(
            "tenant_demo", ToolCall(name=tool.name), user_id=user.id
        )
        task = db.exec(select(ExternalBusinessTask)).one()
        assert result.success is True
        assert result.data["task_id"] == task.id
        assert task.status == "queued"


def test_provider_submission_persists_provider_task_id_and_enters_polling(monkeypatch) -> None:
    """验证供应商受理后写入外部任务号、进入轮询并携带回调相关请求头。"""
    _Client.response_json = {"taskId": "provider-42", "status": "queued"}
    _Client.request_headers = {}
    monkeypatch.setattr("app.tools.tool_executor.httpx.Client", _Client)
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(
            external_task_callback_base_url="https://staffdeck.example"
        ),
    )
    with _session() as db:
        user, tool = _seed(db)
        tool.config_json = {
            "execution": {
                **dict(tool.config_json["execution"]),
                "async_strategy": "provider_task",
            }
        }
        db.add(tool)
        db.commit()

        result = ToolExecutor(db).execute(
            "tenant_demo",
            ToolCall(name=tool.name, arguments={"order": "A-1"}),
            user_id=user.id,
        )
        task = db.exec(select(ExternalBusinessTask)).one()
        assert result.data["task_id"] == task.id

        assert poll_due_external_tasks(db) == 1
        db.refresh(task)
        assert task.external_task_id == "provider-42"
        assert task.status == "accepted"
        assert task.next_poll_at is not None
        assert _Client.request_headers["Idempotency-Key"] == task.idempotency_key
        assert _Client.request_headers["X-StaffDeck-Callback-URL"] == (
            f"https://staffdeck.example/api/external-business-tasks/{task.id}/callback"
        )
        assert _Client.request_headers["X-StaffDeck-Callback-Token"]


def test_provider_submission_rejects_accepted_response_without_task_id(monkeypatch) -> None:
    """验证供应商已受理但缺少任务号时，任务被标记为失败。"""
    _Client.response_json = {"status": "queued"}
    monkeypatch.setattr("app.tools.tool_executor.httpx.Client", _Client)
    with _session() as db:
        user, tool = _seed(db)
        tool.config_json = {
            "execution": {
                **dict(tool.config_json["execution"]),
                "async_strategy": "provider_task",
            }
        }
        db.add(tool)
        db.commit()
        ToolExecutor(db).execute(
            "tenant_demo",
            ToolCall(name=tool.name, arguments={"order": "A-1"}),
            user_id=user.id,
        )
        task = db.exec(select(ExternalBusinessTask)).one()

        poll_due_external_tasks(db)
        db.refresh(task)
        assert task.status == "failed"
        assert task.error_json["code"] == "PROVIDER_TASK_ID_MISSING"


def test_staffdeck_worker_executes_original_sync_tool(monkeypatch) -> None:
    """验证 StaffDeck 工作进程执行原始同步工具并把结果回写到任务。"""
    with _session() as db:
        user, tool = _seed(db)
        task = ExternalBusinessTask(
            tenant_id="tenant_demo", user_id=user.id, tool_id=tool.id,
            external_task_id="exttask-1", status="queued",
            callback_token_hash=callback_token_hash("token"),
            request_json={"employee_id": "12343565"},
        )
        db.add(task)
        db.commit()
        monkeypatch.setattr(
            "app.tools.tool_executor.ToolExecutor.execute_sync_http",
            lambda *_args, **_kwargs: ToolResult(
                tool_name=tool.name,
                success=True,
                data={"remaining": 20000},
            ),
        )
        assert poll_due_external_tasks(db) == 1
        db.refresh(task)
        assert task.status == "completed"
        assert task.result_json == {"remaining": 20000}


def test_expired_submission_lease_does_not_replay_business_request(monkeypatch) -> None:
    """验证提交租约过期后不重放业务请求，任务转为结果未知。"""
    calls: list[dict] = []

    def execute_again(_self, _tool, arguments, **_kwargs):
        """记录被调用时的参数，用于断言业务请求确实没有被重放。"""
        calls.append(dict(arguments))
        return ToolResult(tool_name="orders.submit", success=True, data={"duplicate": True})

    monkeypatch.setattr(ToolExecutor, "execute_sync_http", execute_again)
    with _session() as db:
        user, tool = _seed(db)
        task = ExternalBusinessTask(
            tenant_id="tenant_demo",
            user_id=user.id,
            tool_id=tool.id,
            status="working",
            request_json={"sku": "SKU-1"},
            callback_token_hash=callback_token_hash("token"),
            lease_owner="dead-worker",
            lease_expires_at=tool.created_at,
        )
        db.add(task)
        db.commit()

        assert poll_due_external_tasks(db) == 1
        db.refresh(task)
        assert calls == []
        assert task.status == "outcome_unknown"
        assert task.error_json["code"] == "DETACHED_OUTCOME_UNKNOWN"


def test_callback_auth_dedupe_and_user_owned_query() -> None:
    """验证回调需要任务令牌、重复事件去重，且查询仅限任务属主。"""
    with _session() as db:
        user, tool = _seed(db)
        other = User(
            id="user_other", tenant_id="tenant_demo", username="other", password_hash="x"
        )
        db.add(other)
        token = "opaque-callback-secret"
        task = ExternalBusinessTask(
            id="exttask_1",
            tenant_id="tenant_demo",
            user_id=user.id,
            tool_id=tool.id,
            external_task_id="provider-42",
            status="accepted",
            callback_token_hash=callback_token_hash(token),
        )
        db.add(task)
        db.commit()

        request = ExternalTaskCallback(
            event_id="event-1", status="completed", result={"receipt": "ok"}
        )
        try:
            external_business_task_callback(task.id, request, "wrong-token", db)
        except HTTPException as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("callback must require its per-task credential")
        first = external_business_task_callback(task.id, request, token, db)
        duplicate = external_business_task_callback(task.id, request, token, db)
        assert first == {"accepted": True, "duplicate": False, "status": "completed"}
        assert duplicate["duplicate"] is True
        assert len(db.exec(select(ExternalBusinessTaskEvent)).all()) == 1

        result = get_external_business_task("provider-42", "tenant_demo", None, db, user)
        assert result["status"] == "completed"
        assert result["result"] == {"receipt": "ok"}
        assert get_external_business_task(task.id, "tenant_demo", None, db, user)["id"] == task.id
        try:
            get_external_business_task("provider-42", "tenant_demo", None, db, other)
        except HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("another user must not read this task")


def test_polling_only_updates_external_task_state(monkeypatch) -> None:
    """验证轮询只更新外部任务的状态与结果，不改动其他数据。"""
    with _session() as db:
        user, tool = _seed(db)
        task = ExternalBusinessTask(
            tenant_id="tenant_demo",
            user_id=user.id,
            tool_id=tool.id,
            external_task_id="provider-42",
            status="accepted",
            callback_token_hash=callback_token_hash("token"),
            status_url="https://provider.test/tasks/provider-42",
            status_config_json={
                "status_field": "task.state",
                "result_field": "task.output",
                "status_mapping": {"done": "completed"},
            },
            next_poll_at=tool.created_at,
        )
        db.add(task)
        db.commit()
        monkeypatch.setattr(
            "app.tools.external_tasks.httpx.get",
            lambda *_args, **_kwargs: SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"task": {"state": "done", "output": {"value": 9}}},
            ),
        )

        assert poll_due_external_tasks(db) == 1
        db.refresh(task)
        assert task.status == "completed"
        assert task.result_json == {"value": 9}
        assert task.poll_attempts == 1


def test_completed_external_task_marks_sop_frame_ready_to_resume() -> None:
    """验证外部任务完成后 SOP 帧转为可恢复，并写回结果与槽位。"""
    with _session() as db:
        user, tool = _seed(db)
        session = ChatSession(
            id="session_order",
            tenant_id="tenant_demo",
            user_id=user.id,
            agent_id="agent_order",
        )
        frame = HarnessTaskFrameRecord(
            tenant_id="tenant_demo",
            session_id=session.id,
            source_turn_id="turn_order",
            task_id="sop_order",
            kind="sop",
            status="waiting_external_task",
            skill_id="order_sop",
            step_id="submit_order",
            slots_json={"employee_id": "14534"},
        )
        loop = HarnessAgentLoopRecord(
            id="hloop_order",
            tenant_id="tenant_demo",
            session_id=session.id,
            loop_key=f"sop:{frame.id}",
            kind="sop",
            owner_task_frame_record_id=frame.id,
            checkpoint_json={
                "task_frame_id": frame.task_id,
                "transcript": [
                    {
                        "role": "tool",
                        "tool_name": "orders.submit",
                        "result": {
                            "success": True,
                            "data": {
                                "detached": True,
                                "task_id": "exttask_order_1",
                                "status": "queued",
                            },
                        },
                    }
                ],
            },
        )
        frame.agent_loop_id = loop.id
        task = ExternalBusinessTask(
            id="exttask_order_1",
            tenant_id="tenant_demo",
            user_id=user.id,
            session_id=session.id,
            task_frame_id=frame.task_id,
            tool_id=tool.id,
            external_task_id="provider-order-1",
            status="accepted",
            callback_token_hash=callback_token_hash("token"),
            resume_step_id="confirm_order",
        )
        db.add(session)
        db.add(loop)
        db.add(frame)
        db.add(task)
        db.commit()

        result = external_business_task_callback(
            task.id,
            ExternalTaskCallback(
                event_id="order-complete-1",
                status="completed",
                result={"order_id": "ORD-001"},
            ),
            "token",
            db,
        )

        db.refresh(frame)
        db.refresh(loop)
        assert result["status"] == "completed"
        assert frame.status == "ready_to_resume"
        assert frame.step_id == "confirm_order"
        assert frame.slots_json["order_id"] == "ORD-001"
        assert frame.result_json["external_task_result"] == {"order_id": "ORD-001"}
        assert loop.checkpoint_json["external_task_result"]["status"] == "completed"
        assert (
            loop.checkpoint_json["transcript"][0]["result"]["data"]["result"]
            == {"order_id": "ORD-001"}
        )


def test_fast_completion_does_not_release_running_frame_lease() -> None:
    """验证执行中的帧不会被快速完成的回调抢走租约。"""
    with _session() as db:
        user, tool = _seed(db)
        session = ChatSession(
            id="session_running",
            tenant_id="tenant_demo",
            user_id=user.id,
            agent_id="agent_order",
        )
        frame = HarnessTaskFrameRecord(
            tenant_id="tenant_demo",
            session_id=session.id,
            source_turn_id="turn_order",
            task_id="sop_running",
            kind="sop",
            status="running",
            lease_owner="active-engine",
            lease_expires_at=tool.updated_at,
        )
        task = ExternalBusinessTask(
            tenant_id="tenant_demo",
            user_id=user.id,
            session_id=session.id,
            task_frame_id=frame.task_id,
            tool_id=tool.id,
            status="accepted",
            callback_token_hash=callback_token_hash("token"),
        )
        db.add(session)
        db.add(frame)
        db.add(task)
        db.commit()

        external_business_task_callback(
            task.id,
            ExternalTaskCallback(
                event_id="fast-complete-1",
                status="completed",
                result={"order_id": "ORD-001"},
            ),
            "token",
            db,
        )

        db.refresh(frame)
        assert frame.status == "running"
        assert frame.lease_owner == "active-engine"


def test_completed_conversation_task_does_not_become_ready_to_resume() -> None:
    """验证对话类帧完成后直接结束，不会进入可恢复状态。"""
    with _session() as db:
        user, tool = _seed(db)
        session = ChatSession(
            id="session_conversation",
            tenant_id="tenant_demo",
            user_id=user.id,
            agent_id="agent_order",
        )
        frame = HarnessTaskFrameRecord(
            tenant_id="tenant_demo",
            session_id=session.id,
            source_turn_id="turn_order",
            task_id="conversation_order",
            kind="conversation",
            status="waiting_external_task",
        )
        task = ExternalBusinessTask(
            tenant_id="tenant_demo",
            user_id=user.id,
            session_id=session.id,
            task_frame_id=frame.task_id,
            tool_id=tool.id,
            status="accepted",
            callback_token_hash=callback_token_hash("token"),
        )
        db.add(session)
        db.add(frame)
        db.add(task)
        db.commit()

        external_business_task_callback(
            task.id,
            ExternalTaskCallback(
                event_id="conversation-complete-1",
                status="completed",
                result={"order_id": "ORD-001"},
            ),
            "token",
            db,
        )

        db.refresh(frame)
        assert frame.status == "completed"


def test_ready_sop_frame_is_queued_on_the_next_turn() -> None:
    """验证可恢复的 SOP 帧在下一轮被重新排队并保留槽位。"""
    with _session() as db:
        user, _tool = _seed(db)
        session = ChatSession(
            id="session_resume",
            tenant_id="tenant_demo",
            user_id=user.id,
            agent_id="agent_order",
            active_skill_id="order_sop",
            active_step_id="confirm_order",
        )
        frame = HarnessTaskFrameRecord(
            tenant_id="tenant_demo",
            session_id=session.id,
            source_turn_id="turn_submit",
            task_id="sop_resume",
            kind="sop",
            decision="continue_active",
            status="ready_to_resume",
            skill_id="order_sop",
            step_id="confirm_order",
            slots_json={"order_id": "ORD-001"},
        )
        db.add(session)
        db.add(frame)
        db.commit()

        records = TaskFrameStore(db).persist_plan(
            session,
            "turn_resume",
            TurnPlan(
                decision="switch_to_pending",
                selected_task_id=frame.task_id,
                task_frames=[planned_frame_from_record(frame)],
            ),
        )

        assert len(records) == 1
        assert records[0].status == "queued"
        assert records[0].step_id == "confirm_order"
        assert records[0].slots_json["order_id"] == "ORD-001"
        TaskFrameStore(db).mark_running(records[0])
        assert records[0].status == "running"


def test_create_all_migrates_existing_database_with_external_task_tables() -> None:
    """验证已有数据库建表时会补齐外部任务相关的数据表。"""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE tenants (id VARCHAR PRIMARY KEY, name VARCHAR)"))

    SQLModel.metadata.create_all(engine)

    tables = set(inspect(engine).get_table_names())
    assert {"external_business_tasks", "external_business_task_events"} <= tables


def _seed(db: Session) -> tuple[User, Tool]:
    """写入租户、用户与 detached 执行模式的工具，返回二者供用例复用。"""
    db.add(Tenant(id="tenant_demo", name="Demo"))
    user = User(
        id="user_owner", tenant_id="tenant_demo", username="owner", password_hash="x"
    )
    tool = Tool(
        id="tool_detached",
        tenant_id="tenant_demo",
        name="orders.submit",
        method="POST",
        url="https://provider.test/tasks",
        config_json={
            "execution": {
                "execution_mode": "detached",
                "timeout_seconds": 8,
                "status_url": "https://provider.test/tasks/{taskId}",
                "poll_interval_seconds": 5,
            }
        },
    )
    db.add(user)
    db.add(tool)
    db.commit()
    return user, tool


def _session() -> Session:
    """创建内存 SQLite 会话并建表，确保各用例数据相互隔离。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)
