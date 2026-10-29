from __future__ import annotations

from app.db.models import ModelConfig
from app.llm.model_config_resolver import snapshot_model_config


def skill_model_config(model_config: ModelConfig) -> ModelConfig:
    """为技能调用取得模型配置快照，避免运行期受 ORM 行后续修改影响。

    已是快照的对象原样返回，其余配置对象会被转换为不可变的运行时快照。
    """
    return snapshot_model_config(model_config)
