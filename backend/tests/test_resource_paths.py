from app import paths
from app.core.router import PROMPT_PATH
from app.general_skills.runner import PROMPT_DIR


def test_prompt_path_exists_in_dev() -> None:
    """验证开发态下提示词文件与提示词目录均存在。"""
    assert PROMPT_PATH.exists()
    assert PROMPT_DIR.exists()


def test_resource_dir_hosts_prompts_and_dist_dir() -> None:
    """验证资源目录下包含后端提示词目录。"""
    assert (paths.resource_dir() / "app" / "llm" / "prompts").exists()


def test_mock_server_script_resolves() -> None:
    """验证 mock MCP stdio 服务器脚本可被资源目录解析到。"""
    assert (paths.resource_dir() / "mock_servers" / "mcp_stdio_server.py").exists()
