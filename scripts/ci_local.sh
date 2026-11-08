#!/usr/bin/env bash
# 本地复跑 CI 门禁：后端 ruff + pytest，前端 build + vitest。
#
# 步骤顺序与 .github/workflows/ci.yml 保持一致，便于提交前自查。
# 默认使用 PATH 上的 python3；需要指定解释器时显式传入 PYTHON：
#
#   PYTHON=backend/.venv/bin/python bash scripts/ci_local.sh
#
# pytest-timeout 是 backend[dev] 的可选依赖。未安装时这里会跳过 --timeout，
# 只打印提示，避免本地环境因为缺插件而无法跑门禁。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-python3}"

# CI 里通过 pip install -e "backend[dev]" 安装包；这里补上源码路径，
# 让未安装或使用另一个虚拟环境的本地解释器也能导入 app。
export PYTHONPATH="$REPO_ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"

echo "==> ruff check backend"
"$PYTHON" -m ruff check backend

echo "==> pytest backend/tests"
PYTEST_ARGS=(-m pytest backend/tests -q)
if "$PYTHON" -c "import pytest_timeout" >/dev/null 2>&1; then
    PYTEST_ARGS+=(--timeout=120)
else
    echo "note: pytest-timeout 未安装，跳过 --timeout=120"
    echo "      安装方式：$PYTHON -m pip install -e \"backend[dev]\""
fi
"$PYTHON" "${PYTEST_ARGS[@]}"

echo "==> npm build (frontend-enterprise)"
npm --prefix frontend-enterprise run build

echo "==> npm test (frontend-enterprise)"
npm --prefix frontend-enterprise test -- --run

echo "ci_local: all gates passed"
