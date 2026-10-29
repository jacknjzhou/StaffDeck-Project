import type { ChatAttachmentRead } from '@/types';

import type { ComposerInteractionMode } from './chatTypes';

const CHAT_QUEUE_STORAGE_PREFIX = 'skill_agent_chat_queue';
const INTERACTION_MODES = new Set<ComposerInteractionMode>(['normal', 'scheduled_task']);

type ChatQueueStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export type PreparedChatTurn = {
  queueId: string;
  conversationId: string;
  agentId: string;
  turnId: string;
  text: string;
  attachments: ChatAttachmentRead[];
  interactionMode: ComposerInteractionMode;
  modelConfigId?: string;
  createdAt: string;
};

/** 判断值是否为普通对象（排除 null 与数组），供后续字段校验使用。 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

/** 校验持久化的附件字段是否形如 `ChatAttachmentRead`，作为类型守卫。 */
function isQueuedAttachment(value: unknown): value is ChatAttachmentRead {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === 'string'
    && typeof value.filename === 'string'
    && typeof value.content_type === 'string'
    && typeof value.size === 'number'
    && ['text', 'pdf', 'image', 'binary'].includes(String(value.kind || ''))
  );
}

/**
 * 校验本地队列项是否完整合法，作为 `PreparedChatTurn` 类型守卫。
 *
 * - 逐项检查标识、文本、附件的类型，并限定 `interactionMode` 的取值。
 * - `createdAt` 需为可解析的时间字符串，`modelConfigId` 缺省或为字符串。
 */
function isPreparedChatTurn(value: unknown): value is PreparedChatTurn {
  if (!isRecord(value)) return false;
  return (
    typeof value.queueId === 'string'
    && typeof value.conversationId === 'string'
    && typeof value.agentId === 'string'
    && typeof value.turnId === 'string'
    && typeof value.text === 'string'
    && Array.isArray(value.attachments)
    && value.attachments.every(isQueuedAttachment)
    && typeof value.interactionMode === 'string'
    && INTERACTION_MODES.has(value.interactionMode as ComposerInteractionMode)
    && (value.modelConfigId === undefined || typeof value.modelConfigId === 'string')
    && typeof value.createdAt === 'string'
    && Number.isFinite(Date.parse(value.createdAt))
  );
}

/** 生成按租户与用户隔离的队列存储键，空值分别回退为 default 与 anonymous。 */
export function chatQueueStorageKey(tenantId: string, userId: string): string {
  return `${CHAT_QUEUE_STORAGE_PREFIX}:${tenantId || 'default'}:${userId || 'anonymous'}`;
}

/**
 * 从存储中读取排队中的会话轮次，并按 `queueId:turnId` 去重。
 *
 * - 读取或解析失败时删除该键并返回空数组，避免脏数据阻塞界面。
 * - 若过滤掉了非法条目，会把清洗后的结果回写存储。
 */
export function readQueuedChatTurns(storage: ChatQueueStorage, key: string): PreparedChatTurn[] {
  try {
    const raw = storage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) throw new Error('Invalid chat queue payload');

    const seen = new Set<string>();
    const turns = parsed.filter((value): value is PreparedChatTurn => {
      if (!isPreparedChatTurn(value)) return false;
      const identity = `${value.queueId}:${value.turnId}`;
      if (seen.has(identity)) return false;
      seen.add(identity);
      return true;
    });
    if (turns.length !== parsed.length) {
      writeQueuedChatTurns(storage, key, turns);
    }
    return turns;
  } catch {
    try {
      storage.removeItem(key);
    } catch {
      // Storage access can be blocked by the browser privacy policy.
    }
    return [];
  }
}

/** 将排队轮次写入存储；数组为空时删除对应键，写入失败则尽力清理并返回 false。 */
export function writeQueuedChatTurns(
  storage: ChatQueueStorage,
  key: string,
  turns: PreparedChatTurn[],
): boolean {
  try {
    if (turns.length === 0) {
      storage.removeItem(key);
    } else {
      storage.setItem(key, JSON.stringify(turns));
    }
    return true;
  } catch {
    try {
      storage.removeItem(key);
    } catch {
      // Storage cleanup is best-effort when the browser quota is unavailable.
    }
    return false;
  }
}
