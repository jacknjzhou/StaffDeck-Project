import type { SkillCard, SkillGraphNode } from '../types';

export type SkillFlowPosition = {
  x: number;
  y: number;
};

export type SkillFlowConnectionPreview = {
  sourceNodeId: string;
  targetNodeId: string;
  startX: number;
  startY: number;
  currentX: number;
  currentY: number;
};

export type SkillFlowPoint = {
  x: number;
  y: number;
};

const FLOW_POSITION_KEY = 'flow_position';

/** 读取节点 metadata 中 `flow_position` 记录的流程图坐标；缺失或非法时返回 null。 */
export function skillNodeFlowPosition(node: Record<string, unknown>): SkillFlowPosition | null {
  const metadata = isRecord(node.metadata) ? node.metadata : {};
  const rawPosition = metadata[FLOW_POSITION_KEY];
  if (!isRecord(rawPosition)) return null;
  const x = Number(rawPosition.x);
  const y = Number(rawPosition.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { x, y };
}

/**
 * 返回写入了流程图坐标的技能副本，坐标四舍五入后存入节点 metadata 的 `flow_position`。
 *
 * - 不修改入参，坐标取整后写入副本。
 * - 节点下标越界时原样返回入参。
 */
export function withSkillNodeFlowPosition(
  skill: SkillCard,
  nodeIndex: number,
  position: SkillFlowPosition,
): SkillCard {
  if (nodeIndex < 0 || nodeIndex >= skill.nodes.length) return skill;
  const next = cloneSkill(skill);
  const node = { ...(next.nodes[nodeIndex] || {}) } as SkillGraphNode;
  const metadata = isRecord(node.metadata) ? { ...node.metadata } : {};
  metadata[FLOW_POSITION_KEY] = {
    x: Math.round(position.x),
    y: Math.round(position.y),
  };
  node.metadata = metadata;
  next.nodes[nodeIndex] = node;
  return next;
}

/** 返回移除指定下标连线的技能副本；下标越界时原样返回入参。 */
export function withoutSkillEdgeAt(skill: SkillCard, edgeIndex: number): SkillCard {
  if (edgeIndex < 0 || edgeIndex >= skill.edges.length) return skill;
  const next = cloneSkill(skill);
  next.edges.splice(edgeIndex, 1);
  return next;
}

/**
 * 将滚轮事件增量统一换算为像素值。
 *
 * - `deltaMode` 为 1（按行）时按 16px/行折算，为 2（按页）时按视口尺寸折算。
 * - 其余模式视为已是像素，直接返回原始增量。
 */
export function normalizeSkillFlowWheelDelta(
  delta: number,
  deltaMode: number,
  viewportSize: number,
): number {
  if (deltaMode === 1) return delta * 16;
  if (deltaMode === 2) return delta * Math.max(1, viewportSize);
  return delta;
}

/**
 * 重新锚定连线预览的起点与终点坐标。
 *
 * - 起点总是更新为 `source`；终点仅在传入 `target` 时更新。
 * - `target` 省略时保留原有的 `currentX` / `currentY`。
 */
export function reanchorSkillFlowConnection(
  connection: SkillFlowConnectionPreview,
  source: SkillFlowPoint,
  target?: SkillFlowPoint,
): SkillFlowConnectionPreview {
  return {
    ...connection,
    startX: source.x,
    startY: source.y,
    currentX: target?.x ?? connection.currentX,
    currentY: target?.y ?? connection.currentY,
  };
}

/** 通过 JSON 序列化深拷贝技能数据，避免后续修改影响入参对象。 */
function cloneSkill(skill: SkillCard): SkillCard {
  return JSON.parse(JSON.stringify(skill)) as SkillCard;
}

/** 类型守卫：判断值是否为非数组的普通对象。 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}
