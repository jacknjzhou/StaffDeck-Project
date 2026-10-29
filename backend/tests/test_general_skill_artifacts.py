from pathlib import Path

from app.general_skills.runner import _normalize_declared_artifacts


def test_general_skill_artifacts_become_task_relative_paths(tmp_path: Path) -> None:
    """验证产物路径被改写为相对任务根目录的路径。"""
    task_root = tmp_path / "task"
    artifact_root = task_root / "general_skill_abc" / "artifacts"
    artifact_root.mkdir(parents=True)
    structured = {"success": True, "artifacts": [{"path": "report.xlsx"}]}

    _normalize_declared_artifacts(
        structured,
        artifact_root=artifact_root,
        workspace_root=task_root,
    )

    assert structured["artifacts"] == [
        {"path": "general_skill_abc/artifacts/report.xlsx"}
    ]


def test_general_skill_rejects_absolute_artifact_paths_without_failing_run(tmp_path: Path) -> None:
    """验证绝对路径产物被拒绝并留下错误记录，但不把整次运行判为失败。"""
    task_root = tmp_path / "task"
    artifact_root = task_root / "general_skill_abc" / "artifacts"
    artifact_root.mkdir(parents=True)
    structured = {"success": True, "artifacts": [{"path": "/workspace/report.xlsx"}]}

    _normalize_declared_artifacts(
        structured,
        artifact_root=artifact_root,
        workspace_root=task_root,
    )

    assert structured["success"] is True
    assert structured["artifacts"] == []
    assert structured["artifact_errors"] == [
        {
            "path": "/workspace/report.xlsx",
            "code": "artifact_declaration_invalid",
            "message": "产物路径必须位于当前运行目录，且只能使用相对路径。",
        }
    ]


def test_general_skill_keeps_valid_artifacts_when_another_declaration_is_invalid(
    tmp_path: Path,
) -> None:
    """验证仅非法产物被丢弃，合法产物与运行成功状态都保留。"""
    task_root = tmp_path / "task"
    artifact_root = task_root / "general_skill_abc" / "artifacts"
    artifact_root.mkdir(parents=True)
    structured = {
        "success": True,
        "artifacts": [
            {"path": "valid.csv"},
            {"path": "/workspace/invalid.csv"},
            {"path": "nested/result.json"},
        ],
    }

    _normalize_declared_artifacts(
        structured,
        artifact_root=artifact_root,
        workspace_root=task_root,
    )

    assert structured["success"] is True
    assert structured["artifacts"] == [
        {"path": "general_skill_abc/artifacts/valid.csv"},
        {"path": "general_skill_abc/artifacts/nested/result.json"},
    ]
    assert [item["path"] for item in structured["artifact_errors"]] == [
        "/workspace/invalid.csv"
    ]
