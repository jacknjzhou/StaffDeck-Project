import { useState, type ReactNode } from 'react';

import CodeBlock from '@/components/CodeBlock';
import { ApiError } from '@/api/client';
import type { StreamEvent } from '@/api/client';
import { formatClientDateTime } from '@/lib/timezone';
import type {
  ChatAttachmentRead,
  ChatMessage,
  ChatSession,
  ChatSessionEventRead,
  HarnessWorkspaceArtifact,
  KnowledgeCitation,
  ScheduledTaskDraftRead,
  ScheduledTaskRead,
  UIConfigRead,
} from '@/types';

import {
  CHAT_MARKDOWN_CLASS,
  CHAT_MARKDOWN_IMAGE_CLASS,
  CHAT_MARKDOWN_IMAGE_LINK_CLASS,
  CHAT_MD_TABLE_CLASS,
  CHAT_MD_TABLE_SCROLL_CLASS,
} from './chatPageStyles';
import type {
  ComposerAttachment,
  CotTraceIconName,
  DraftScheduleType,
  SessionSlot,
  TraceLine,
  TraceSkill,
  TraceTool,
  TurnTrace,
} from './chatTypes';
export {
  SELECTED_AGENT_STORAGE_KEY,
  SESSION_FILTER_STORAGE_PREFIX,
  sessionFilterStorageKey,
} from '@/lib/agent-scope-storage';

export const MODEL_CONFIG_STORAGE_PREFIX = 'skill_agent_selected_model_config';
export const SESSION_READ_STORAGE_PREFIX = 'skill_agent_session_read_at';
export const SIDEBAR_COLLAPSED_STORAGE_KEY = 'skill_agent_sidebar_collapsed';
export const RUNNING_EVENT_RECOVERY_WINDOW_MS = 600 * 1000;
export const CHAT_STREAM_IDLE_TIMEOUT_MS = 600 * 1000;
export const CHAT_STREAM_IDLE_CHECK_INTERVAL_MS = 5 * 1000;
export const CHAT_STREAM_HEARTBEAT_GRACE_MS = 20 * 1000;
export const CHAT_TRACE_RECOVERY_WINDOW_MS = 10 * 60 * 1000;
export const STREAM_TERMINAL_EVENTS = new Set(['complete', 'done', 'stream_end', 'stream_cancelled', 'stream_interrupted', 'error', 'error_occurred']);
export const HIDDEN_GENERAL_SKILL_TRACE_PHASES = new Set(['replying']);
const DRAFT_SCHEDULE_TYPES = new Set<DraftScheduleType>(['once', 'daily', 'weekly', 'monthly']);
const DRAFT_SCHEDULE_TYPE_LABELS: Record<DraftScheduleType, string> = {
  once: '一次性',
  daily: '每天',
  weekly: '每周',
  monthly: '每月',
};
const DRAFT_WEEKDAY_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

/** 生成按用户隔离的会话已读时间 localStorage 键，userId 为空时回退到 anonymous。 */
export function sessionReadStorageKey(userId: string): string {
  return `${SESSION_READ_STORAGE_PREFIX}:${userId || 'anonymous'}`;
}

/** 从 localStorage 读取会话已读时间映射，解析失败或数据非法时返回空对象。 */
export function loadSessionReadTimes(userId: string): Record<string, string> {
  try {
    const raw = window.localStorage.getItem(sessionReadStorageKey(userId));
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, string>;
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

/** 将会话已读时间映射序列化写入当前用户的 localStorage 键。 */
export function persistSessionReadTimes(userId: string, values: Record<string, string>): void {
  window.localStorage.setItem(sessionReadStorageKey(userId), JSON.stringify(values));
}

/** 判断会话是否为定时任务触发的会话。 */
export function isScheduledSession(session: ChatSession): boolean {
  return session.is_scheduled === true;
}

/**
 * 判断会话是否存在未读的新回复（用于侧栏未读标记）。
 *
 * 跳过当前激活会话、运行中会话与无摘要会话；更新时间需比已读时间晚 1 秒以上。
 */
export function sessionHasUnreadReply(
  session: ChatSession,
  readTimes: Record<string, string>,
  activeSessionId?: string,
): boolean {
  if (session.id === activeSessionId) return false;
  const summary = session.summary || session.last_agent_question || '';
  if (!summary) return false;
  if (session.status === 'running' || session.status === 'executing') return false;
  const updatedAt = Date.parse(session.updated_at || '');
  const readAt = Date.parse(readTimes[session.id] || '');
  return Number.isFinite(updatedAt) && (!Number.isFinite(readAt) || updatedAt > readAt + 1000);
}

/** 生成指定智能体的草稿会话标识，用于尚未落库的新会话。 */
export function draftConversationKey(agentId: string): string {
  return `draft:${agentId}`;
}

/** 判断会话标识是否为草稿会话（以 draft: 前缀）。 */
export function isDraftConversationKey(id: string): boolean {
  return id.startsWith('draft:');
}

/** 判断错误是否为表示会话不存在的 404 API 错误。 */
export function isMissingChatSessionError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/** 生成按租户隔离的模型选择 localStorage 键。 */
export function modelStorageKey(tenantId: string): string {
  return `${MODEL_CONFIG_STORAGE_PREFIX}:${tenantId}`;
}

/** 返回模型的展示名称，缺失时依次回退到模型标识与「模型」。 */
export function modelDisplayName(model: { name?: string; model?: string }): string {
  return (model.name || model.model || '模型').trim();
}

/** 返回模型副标题（模型标识或供应商），默认模型追加「默认」后缀。 */
export function modelDetailText(model: { name?: string; model?: string; provider?: string; is_default?: boolean }): string {
  const detail = model.model && model.model !== model.name ? model.model : model.provider || '';
  return model.is_default ? `${detail} · 默认` : detail;
}

/** 将文本中的连续空白折叠为单空格并去除首尾空白，非字符串返回空串。 */
export function normalizeMessageText(value?: string): string {
  return typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : '';
}

/** 判断流式文本规范化后是否已有至少 2 个字符可渲染。 */
export function hasRenderableStreamingText(value?: string): boolean {
  return Array.from(normalizeMessageText(value)).length >= 2;
}

/** 判断消息是否为排队等待发送的用户消息。 */
export function isQueuedChatMessage(messageItem: ChatMessage): boolean {
  return messageItem.role === 'user' && messageItem.metadata?.queued === true;
}

/** 将排队中的用户消息按 turn 去重后移动到时间线末尾。 */
export function placeQueuedMessagesLast(messages: ChatMessage[]): ChatMessage[] {
  const timeline: ChatMessage[] = [];
  const queued: ChatMessage[] = [];
  const queuedTurnIds = new Set<string>();

  messages.forEach((messageItem) => {
    if (!isQueuedChatMessage(messageItem)) {
      timeline.push(messageItem);
      return;
    }
    const identity = messageItem.turnId || messageItem.id;
    if (queuedTurnIds.has(identity)) return;
    queuedTurnIds.add(identity);
    queued.push(messageItem);
  });

  return [...timeline, ...queued];
}

/** 将纯文本中的裸 URL 渲染为外链节点，非 http(s) 链接保留为文本。 */
function renderBareLinks(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(?:https?:\/\/|www\.)[^\s<>"'`]+/gi;
  const trailingPunctuation = /[.,!?;:\uff0c\u3002\uff01\uff1f\uff1b\uff1a\u3001)\]}>]+$/;
  let cursor = 0;
  let index = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    const candidate = match[0];
    const label = candidate.replace(trailingPunctuation, '');
    if (!label) continue;
    const href = safeExternalHttpUrl(/^www\./i.test(label) ? `https://${label}` : label);
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index));
    if (href) {
      nodes.push(
        <a key={`${keyPrefix}-url-${index}`} href={href} target="_blank" rel="noreferrer">
          {label}
        </a>,
      );
    } else {
      nodes.push(label);
    }
    const trailing = candidate.slice(label.length);
    if (trailing) nodes.push(trailing);
    cursor = match.index + candidate.length;
    index += 1;
  }

  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

export type MarkdownRenderOptions = {
  renderInternalLink?: (link: { label: string; href: string; key: string }) => ReactNode;
};

/** 校验并规范化 URL，仅返回 http/https 协议的地址，非法输入返回 null。 */
function safeExternalHttpUrl(value: string): string | null {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    return parsed.href;
  } catch {
    return null;
  }
}

/** 渲染 Markdown 外部图片，加载失败时退化为替代文本。 */
function ExternalMarkdownImage({ alt, src }: { alt: string; src: string }) {
  const [failed, setFailed] = useState(false);

  if (failed) {
    return <span title={src}>{alt}</span>;
  }

  return (
    <a
      className={CHAT_MARKDOWN_IMAGE_LINK_CLASS}
      href={src}
      target="_blank"
      rel="noreferrer"
      aria-label={`查看图片：${alt}`}
    >
      <img
        className={CHAT_MARKDOWN_IMAGE_CLASS}
        src={src}
        alt={alt}
        loading="lazy"
        decoding="async"
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
      />
    </a>
  );
}

/**
 * 渲染行内 Markdown（代码、加粗、图片、链接、裸链接）为 React 节点数组。
 *
 * 仅放行 http/https 图片与链接；内部链接交给 options.renderInternalLink 处理。
 */
export function renderInlineMarkdown(
  text: string,
  keyPrefix: string,
  options: MarkdownRenderOptions = {},
): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(`[^`]*`|\*\*[^*]+?\*\*|!?\[[^\]\n]*\]\((?:[^()\n]|\([^()\n]*\))+\))/g;
  let cursor = 0;
  let index = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(...renderBareLinks(text.slice(cursor, match.index), `${keyPrefix}-${index}`));
    }
    const token = match[0];
    const key = `${keyPrefix}-inline-${index}`;
    if (token.startsWith('`') && token.endsWith('`')) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith('**') && token.endsWith('**')) {
      nodes.push(<strong key={key}>{renderInlineMarkdown(token.slice(2, -2), key, options)}</strong>);
    } else {
      const image = token.match(/^!\[([^\]]*)\]\(((?:[^()\n]|\([^()\n]*\))+)\)$/);
      if (image) {
        const alt = image[1].trim() || '图片';
        const src = safeExternalHttpUrl(image[2].trim());
        if (src) {
          nodes.push(
            <ExternalMarkdownImage key={key} src={src} alt={alt} />,
          );
        } else {
          nodes.push(<span key={key}>{alt}</span>);
        }
        cursor = match.index + token.length;
        index += 1;
        continue;
      }
      const link = token.match(/^\[([^\]]*)\]\(((?:[^()\n]|\([^()\n]*\))+)\)$/);
      if (link) {
        const href = link[2].trim();
        const label = link[1] || href;
        const safeHref = safeExternalHttpUrl(href);
        if (safeHref) {
          nodes.push(
            <a key={key} href={safeHref} target="_blank" rel="noreferrer">
              {label}
            </a>,
          );
        } else if (options.renderInternalLink) {
          nodes.push(options.renderInternalLink({ label, href, key }));
        } else {
          nodes.push(
            <span key={key} className="md-link-label" title={href}>
              {label}
            </span>,
          );
        }
      } else {
        nodes.push(token);
      }
    }
    cursor = match.index + token.length;
    index += 1;
  }

  if (cursor < text.length) {
    nodes.push(...renderBareLinks(text.slice(cursor), `${keyPrefix}-${index}`));
  }
  return nodes;
}

/** 计算软换行分隔符：相邻两侧为 CJK 字符时不补空格，否则补一个空格。 */
function softLineBreakSeparator(previousLine: string, currentLine: string): string {
  const previous = previousLine.trimEnd();
  const current = currentLine.trimStart();
  if (!previous || !current) return '';

  const previousCharacter = previous.charAt(previous.length - 1);
  const currentCharacter = current.charAt(0);
  const cjkCharacter = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u;
  return cjkCharacter.test(previousCharacter) || cjkCharacter.test(currentCharacter) ? '' : ' ';
}

/** 逐行渲染行内 Markdown，并按需插入换行元素或软换行分隔符。 */
function renderInlineLines(
  lines: string[],
  keyPrefix: string,
  preserveLineBreaks: boolean,
  options: MarkdownRenderOptions,
): ReactNode[] {
  return lines.flatMap((line, lineIndex) => {
    const renderedLine = preserveLineBreaks ? line : line.trim();
    const nodes = renderInlineMarkdown(renderedLine, `${keyPrefix}-line-${lineIndex}`, options);
    if (lineIndex === 0) return nodes;
    const separator = preserveLineBreaks
      ? <br key={`${keyPrefix}-br-${lineIndex}`} />
      : softLineBreakSeparator(lines[lineIndex - 1], line);
    return [separator, ...nodes];
  });
}

type MarkdownTableAlign = 'left' | 'center' | 'right';

/** 按未转义且不在行内代码中的竖线拆分表格行，并去除首尾竖线。 */
function splitMarkdownTableRow(row: string): string[] {
  let text = row.trim();
  if (text.startsWith('|')) text = text.slice(1);
  if (text.endsWith('|')) text = text.slice(0, -1);

  const cells: string[] = [];
  let current = '';
  let inCode = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === '`') {
      inCode = !inCode;
      current += char;
      continue;
    }
    if (char === '\\' && text[index + 1] === '|') {
      current += '|';
      index += 1;
      continue;
    }
    if (char === '|' && !inCode) {
      cells.push(current.trim());
      current = '';
      continue;
    }
    current += char;
  }
  cells.push(current.trim());
  return cells;
}

/** 判断一行是否为 Markdown 表格分隔行（至少两列且每列形如 ---）。 */
function isMarkdownTableSeparator(line: string): boolean {
  const cells = splitMarkdownTableRow(line);
  return cells.length >= 2 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s+/g, '')));
}

/** 从表格分隔单元格解析对齐方式，默认左对齐。 */
function markdownTableAlign(separatorCell: string): MarkdownTableAlign {
  const normalized = separatorCell.replace(/\s+/g, '');
  if (normalized.startsWith(':') && normalized.endsWith(':')) return 'center';
  if (normalized.endsWith(':')) return 'right';
  return 'left';
}

/** 判断从 index 行起是否为 Markdown 表格起始（表头 + 分隔行）。 */
function isMarkdownTableStart(lines: string[], index: number): boolean {
  if (index + 1 >= lines.length) return false;
  const header = lines[index].trim();
  if (!header.includes('|')) return false;
  return splitMarkdownTableRow(header).length >= 2 && isMarkdownTableSeparator(lines[index + 1]);
}

/**
 * 渲染 Markdown 表格，并返回消费到的下一行下标。
 *
 * 遇到空行、非表格行或第二个分隔行时停止收集数据行；单元格补足到最大列数。
 */
function renderMarkdownTable(
  lines: string[],
  startIndex: number,
  key: string,
  options: MarkdownRenderOptions,
): { node: ReactNode; nextIndex: number } {
  const header = splitMarkdownTableRow(lines[startIndex]);
  const separator = splitMarkdownTableRow(lines[startIndex + 1]);
  const aligns = separator.map(markdownTableAlign);
  const rows: string[][] = [];
  let index = startIndex + 2;

  while (index < lines.length) {
    const row = lines[index].trim();
    if (!row || !row.includes('|') || isMarkdownTableSeparator(row)) break;
    const cells = splitMarkdownTableRow(row);
    if (cells.length < 2) break;
    rows.push(cells);
    index += 1;
  }

  const columnCount = Math.max(header.length, separator.length, ...rows.map((row) => row.length));
  const cellStyle = (cellIndex: number) => ({ textAlign: (aligns[cellIndex] || 'left') as MarkdownTableAlign });
  const renderCells = (cells: string[], rowKey: string) =>
    Array.from({ length: columnCount }, (_, cellIndex) => (
      <td key={`${rowKey}-${cellIndex}`} style={cellStyle(cellIndex)}>
        {renderInlineMarkdown(cells[cellIndex] || '', `${rowKey}-${cellIndex}`, options)}
      </td>
    ));

  return {
    nextIndex: index,
    node: (
      <div key={key} className={CHAT_MD_TABLE_SCROLL_CLASS}>
        <table className={CHAT_MD_TABLE_CLASS}>
          <thead>
            <tr>
              {Array.from({ length: columnCount }, (_, cellIndex) => (
                <th key={`${key}-head-${cellIndex}`} style={cellStyle(cellIndex)}>
                  {renderInlineMarkdown(header[cellIndex] || '', `${key}-head-${cellIndex}`, options)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={`${key}-row-${rowIndex}`}>{renderCells(row, `${key}-row-${rowIndex}`)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    ),
  };
}

/** 判断一行是否开启新块（代码块、分隔线、标题、引用、列表）。 */
function isBlockBoundary(line: string): boolean {
  const trimmed = line.trim();
  return (
    trimmed.startsWith('```') ||
    /^(-{3,}|\*{3,}|_{3,})$/.test(trimmed) ||
    /^#{1,6}\s+/.test(trimmed) ||
    /^>\s?/.test(trimmed) ||
    /^[-*]\s+/.test(trimmed) ||
    /^\d+[.)]\s+/.test(trimmed)
  );
}

/**
 * 将 Markdown 文本解析为块级 React 节点（代码块、标题、引用、表格、列表、段落）。
 *
 * preserveLineBreaks 为 true 时段落内保留换行，否则按软换行拼接；
 * 有序列表跨段落时延续编号。
 */
export function renderMarkdownBlocks(
  content: string,
  preserveLineBreaks = true,
  options: MarkdownRenderOptions = {},
): ReactNode[] {
  const lines = content.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let index = 0;
  let blockIndex = 0;
  let continuedOrderedListStart: number | null = null;

  const resetOrderedListSequence = () => {
    continuedOrderedListStart = null;
  };

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();
    const key = `md-${blockIndex}`;
    if (!trimmed) {
      index += 1;
      continue;
    }

    if (trimmed.startsWith('```')) {
      resetOrderedListSequence();
      const language = trimmed.slice(3).trim();
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(
        <CodeBlock key={key} className="md-code-block" code={codeLines.join('\n')} language={language || undefined} />,
      );
      blockIndex += 1;
      continue;
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      resetOrderedListSequence();
      blocks.push(<hr key={key} />);
      index += 1;
      blockIndex += 1;
      continue;
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      resetOrderedListSequence();
      const level = Math.min(heading[1].length, 4) as 1 | 2 | 3 | 4;
      const Tag = `h${level}` as keyof JSX.IntrinsicElements;
      blocks.push(<Tag key={key}>{renderInlineMarkdown(heading[2], key, options)}</Tag>);
      index += 1;
      blockIndex += 1;
      continue;
    }

    if (/^>\s?/.test(trimmed)) {
      resetOrderedListSequence();
      const quoteLines: string[] = [];
      while (index < lines.length && /^>\s?/.test(lines[index].trim())) {
        quoteLines.push(lines[index].trim().replace(/^>\s?/, ''));
        index += 1;
      }
      blocks.push(
        <blockquote key={key}>
          {renderMarkdownBlocks(quoteLines.join('\n'), preserveLineBreaks, options)}
        </blockquote>,
      );
      blockIndex += 1;
      continue;
    }

    if (isMarkdownTableStart(lines, index)) {
      resetOrderedListSequence();
      const table = renderMarkdownTable(lines, index, key, options);
      blocks.push(table.node);
      index = table.nextIndex;
      blockIndex += 1;
      continue;
    }

    if (/^[-*]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^[-*]\s+/, ''));
        index += 1;
      }
      blocks.push(
        <ul key={key}>
          {items.map((item, itemIndex) => (
            <li key={`${key}-${itemIndex}`}>
              {renderInlineMarkdown(item, `${key}-${itemIndex}`, options)}
            </li>
          ))}
        </ul>,
      );
      blockIndex += 1;
      continue;
    }

    if (/^\d+[.)]\s+/.test(trimmed)) {
      const items: Array<{ marker: number; content: string }> = [];
      while (index < lines.length && /^\d+[.)]\s+/.test(lines[index].trim())) {
        const item = lines[index].trim().match(/^(\d+)[.)]\s+(.+)$/);
        if (!item) break;
        items.push({ marker: Number(item[1]), content: item[2] });
        index += 1;
      }
      const explicitStart = items[0]?.marker || 1;
      const listStart: number = explicitStart === 1 && continuedOrderedListStart !== null
        ? continuedOrderedListStart
        : explicitStart;
      blocks.push(
        <ol key={key} start={listStart === 1 ? undefined : listStart}>
          {items.map((item, itemIndex) => (
            <li key={`${key}-${itemIndex}`}>
              {renderInlineMarkdown(item.content, `${key}-${itemIndex}`, options)}
            </li>
          ))}
        </ol>,
      );
      continuedOrderedListStart = listStart + items.length;
      blockIndex += 1;
      continue;
    }

    resetOrderedListSequence();
    const paragraphLines: string[] = [];
    while (
      index < lines.length &&
      lines[index].trim() &&
      !isBlockBoundary(lines[index]) &&
      !isMarkdownTableStart(lines, index)
    ) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    blocks.push(
      <p key={key}>{renderInlineLines(paragraphLines, key, preserveLineBreaks, options)}</p>,
    );
    blockIndex += 1;
  }

  return blocks;
}

/**
 * Markdown 消息渲染组件。
 *
 * 透传 preserveLineBreaks 控制段落内是否保留换行，输出统一样式的 Markdown 容器。
 */
export function MarkdownMessage({
  content,
  preserveLineBreaks = true,
}: {
  content: string;
  preserveLineBreaks?: boolean;
}) {
  return <div className={CHAT_MARKDOWN_CLASS}>{renderMarkdownBlocks(content, preserveLineBreaks)}</div>;
}

/** 返回执行记录摘要固定使用的图标名。 */
export function traceSummaryIconName(_summary: { state: TraceLine['state'] }): CotTraceIconName {
  return 'execute';
}

/** 按执行记录行类型推断图标，优先使用行自带的 icon。 */
export function traceLineIconName(line: TraceLine): CotTraceIconName {
  if (line.icon) return line.icon;
  if (line.kind === 'decision') return 'judge';
  if (line.kind === 'tool') return 'tool';
  if (line.kind === 'code') return 'generated';
  if (line.kind === 'thinking') return 'loading';
  return 'advance';
}

/** 解析消息时间戳为毫秒数，缺省时区按 UTC 处理，非法值返回 0。 */
export function parseMessageTime(value?: string): number {
  if (!value) return 0;
  const normalized = /(?:z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const time = Date.parse(normalized);
  return Number.isFinite(time) ? time : 0;
}

/** 向别名数组追加非空且未重复的字符串别名。 */
function appendTurnAlias(aliases: string[], value: unknown): void {
  if (typeof value !== 'string') return;
  const normalized = value.trim();
  if (normalized && !aliases.includes(normalized)) aliases.push(normalized);
}

/** 读取消息 metadata 中的非空字符串字段并去除首尾空白。 */
function metadataString(messageItem: ChatMessage, key: string): string | undefined {
  const value = messageItem.metadata?.[key];
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

/** 汇总消息上可作为 turn 别名的各种标识，用于合并同一轮次的消息。 */
export function messageTurnAliases(messageItem: ChatMessage): string[] {
  const aliases: string[] = [];
  appendTurnAlias(aliases, messageItem.turnId);
  appendTurnAlias(aliases, messageItem.turn_id);
  appendTurnAlias(aliases, metadataString(messageItem, 'turn_id'));
  appendTurnAlias(aliases, metadataString(messageItem, 'user_message_id'));
  appendTurnAlias(aliases, metadataString(messageItem, 'client_turn_id'));
  appendTurnAlias(aliases, messageItem.serverMessageId);
  if (messageItem.role === 'user') appendTurnAlias(aliases, messageItem.id);
  return aliases;
}

/** 选择作为并查集根的首选别名，服务端用户消息优先使用其自身 id。 */
function preferredTurnAlias(messageItem: ChatMessage, aliases: string[]): string | undefined {
  if (messageItem.role === 'user' && !messageItem.id.startsWith('local_')) return messageItem.id;
  return aliases[0];
}

/**
 * 基于并查集构建 turn 别名到规范 turn id 的映射。
 *
 * 同一轮次的消息通过 turn_id、serverMessageId 等别名合并，输出每个别名的根 id。
 */
export function buildTurnAliasMap(messages: ChatMessage[]): Map<string, string> {
  const parent = new Map<string, string>();

  const find = (value: string): string => {
    const current = parent.get(value);
    if (!current) {
      parent.set(value, value);
      return value;
    }
    if (current === value) return value;
    const root = find(current);
    parent.set(value, root);
    return root;
  };

  const union = (canonical: string, alias: string) => {
    const canonicalRoot = find(canonical);
    const aliasRoot = find(alias);
    if (canonicalRoot !== aliasRoot) {
      parent.set(aliasRoot, canonicalRoot);
    }
  };

  messages.forEach((messageItem) => {
    const aliases = messageTurnAliases(messageItem);
    const canonical = preferredTurnAlias(messageItem, aliases);
    if (!canonical) return;
    aliases.forEach((alias) => union(canonical, alias));
  });

  const result = new Map<string, string>();
  parent.forEach((_value, key) => {
    result.set(key, find(key));
  });
  return result;
}

/** 将 turn id 归一化为别名映射中的规范 id，空值返回 undefined。 */
export function canonicalTurnIdForValue(turnId: string | null | undefined, aliasMap: Map<string, string>): string | undefined {
  const normalized = typeof turnId === 'string' ? turnId.trim() : '';
  if (!normalized) return undefined;
  return aliasMap.get(normalized) || normalized;
}

/** 返回消息对应的规范 turn id，无别名命中时回退到显式或用户消息 id。 */
export function canonicalMessageTurnId(messageItem: ChatMessage, aliasMap: Map<string, string>): string | undefined {
  const aliases = messageTurnAliases(messageItem);
  for (const alias of aliases) {
    const canonical = aliasMap.get(alias);
    if (canonical) return canonical;
  }
  return effectiveMessageTurnId(messageItem);
}

/** 在会话槽位中查找指定轮次最近的一条用户消息。 */
function latestUserMessageForTurn(slot: SessionSlot, turnId?: string | null): ChatMessage | undefined {
  const messages = [...slot.serverMessages, ...slot.realtimeMessages];
  const aliasMap = buildTurnAliasMap(messages);
  const canonicalTurnId = canonicalTurnIdForValue(turnId, aliasMap);
  const scoped = messages.filter((messageItem) => (
    messageItem.role === 'user'
    && (!canonicalTurnId || canonicalMessageTurnId(messageItem, aliasMap) === canonicalTurnId)
  ));
  const candidates = scoped.length
    ? scoped
    : messages.filter((messageItem) => messageItem.role === 'user');
  return candidates.sort((left, right) => parseMessageTime(right.created_at) - parseMessageTime(left.created_at))[0];
}

/** 生成略晚于给定消息的时间戳，用作占位消息的创建时间。 */
function timestampAfterMessage(messageItem?: ChatMessage): string {
  const baseTime = messageItem ? parseMessageTime(messageItem.created_at) : 0;
  return new Date((baseTime > 0 ? baseTime : Date.now()) + 1).toISOString();
}

/** 判断服务端消息中是否已存在同轮次同角色的消息。 */
function hasServerMessageForTurn(messageItem: ChatMessage, serverMessages: ChatMessage[]): boolean {
  const messages = [...serverMessages, messageItem];
  const aliasMap = buildTurnAliasMap(messages);
  const messageTurnId = canonicalMessageTurnId(messageItem, aliasMap);
  if (!messageTurnId) return false;
  return serverMessages.some(
    (serverMessage) => (
      canonicalMessageTurnId(serverMessage, aliasMap) === messageTurnId
      && serverMessage.role === messageItem.role
    ),
  );
}

/** 判断两条消息是否属于同一轮次且角色相同。 */
export function sameRoleTurn(left: ChatMessage, right: ChatMessage): boolean {
  const aliasMap = buildTurnAliasMap([left, right]);
  const leftTurnId = canonicalMessageTurnId(left, aliasMap);
  const rightTurnId = canonicalMessageTurnId(right, aliasMap);
  return Boolean(leftTurnId && rightTurnId && leftTurnId === rightTurnId && left.role === right.role);
}

/** 判断指定轮次是否已有带内容的非流式助手回复。 */
export function hasAssistantMessageForTurn(slot: SessionSlot, turnId: string): boolean {
  if (!turnId) return false;
  const messages = [...slot.serverMessages, ...slot.realtimeMessages];
  const aliasMap = buildTurnAliasMap(messages);
  const canonicalTurnId = canonicalTurnIdForValue(turnId, aliasMap);
  return messages.some((messageItem) => (
    messageItem.role === 'assistant'
    && !messageItem.isStreaming
    && canonicalMessageTurnId(messageItem, aliasMap) === canonicalTurnId
    && Boolean(normalizeMessageText(messageItem.content))
  ));
}

/** 判断指定轮次是否已有助手载体消息（含错误或执行记录占位）。 */
export function hasAssistantCarrierForTurn(slot: SessionSlot, turnId: string): boolean {
  if (!turnId) return false;
  const messages = [...slot.serverMessages, ...slot.realtimeMessages];
  const aliasMap = buildTurnAliasMap(messages);
  const canonicalTurnId = canonicalTurnIdForValue(turnId, aliasMap);
  return messages.some((messageItem) => (
    messageItem.role === 'assistant'
    && !messageItem.isStreaming
    && canonicalMessageTurnId(messageItem, aliasMap) === canonicalTurnId
    && (
      Boolean(normalizeMessageText(messageItem.content))
      || messageItem.isError
      || messageItem.id.startsWith('__trace_')
    )
  ));
}

/** 生成流式占位消息 id，传入 turnId 时按轮次区分。 */
export function streamingMessageId(sessionId: string, turnId?: string | null): string {
  const normalizedTurnId = typeof turnId === 'string' ? turnId.trim() : '';
  return normalizedTurnId ? `__streaming_${sessionId}_${normalizedTurnId}` : `__streaming_${sessionId}`;
}

/** 判断消息 id 是否为指定会话的流式占位消息。 */
export function isStreamingMessageId(messageId: string, sessionId: string): boolean {
  const prefix = `__streaming_${sessionId}`;
  return messageId === prefix || messageId.startsWith(`${prefix}_`);
}

/**
 * 为指定轮次插入或更新流式回复占位消息。
 *
 * 就地替换 slot.realtimeMessages；内容无变化时返回 false，避免触发多余重渲染。
 */
export function upsertStreamingTracePlaceholder(slot: SessionSlot, sessionId: string, turnId: string): boolean {
  if (!turnId) return false;
  const streamId = streamingMessageId(sessionId, turnId);
  const streamingMessage: ChatMessage = {
    id: streamId,
    turnId,
    role: 'assistant',
    content: '',
    created_at: timestampAfterMessage(latestUserMessageForTurn(slot, turnId)),
    isStreaming: true,
  };
  const index = slot.realtimeMessages.findIndex((item) => item.id === streamId);
  if (index >= 0) {
    const current = slot.realtimeMessages[index];
    if (
      current.turnId === streamingMessage.turnId
      && current.isStreaming
      && current.content === streamingMessage.content
    ) {
      return false;
    }
    slot.realtimeMessages = [...slot.realtimeMessages];
    slot.realtimeMessages[index] = { ...current, ...streamingMessage, created_at: current.created_at || streamingMessage.created_at };
    return true;
  }
  slot.realtimeMessages = [...slot.realtimeMessages, streamingMessage];
  return true;
}

/**
 * 为指定轮次插入或更新执行记录（trace）占位消息。
 *
 * 已有同轮次助手消息时返回 false；流式占位会被原地改造成 trace 占位。
 */
export function upsertTraceStatusPlaceholder(slot: SessionSlot, sessionId: string, turnId: string): boolean {
  if (!turnId) return false;
  const traceId = `__trace_${sessionId}_${turnId}`;
  const streamId = streamingMessageId(sessionId, turnId);
  const traceMessage: ChatMessage = {
    id: traceId,
    turnId,
    role: 'assistant',
    content: '',
    created_at: timestampAfterMessage(latestUserMessageForTurn(slot, turnId)),
    isStreaming: false,
  };
  const existingAliasMap = buildTurnAliasMap([...slot.serverMessages, ...slot.realtimeMessages, traceMessage]);
  const canonicalTraceTurnId = canonicalTurnIdForValue(turnId, existingAliasMap);
  const existingAssistantIndex = slot.realtimeMessages.findIndex((item) => (
    item.role === 'assistant'
    && item.id !== traceId
    && item.id !== streamId
    && canonicalMessageTurnId(item, existingAliasMap) === canonicalTraceTurnId
  ));
  if (existingAssistantIndex >= 0) return false;
  const index = slot.realtimeMessages.findIndex((item) => item.id === traceId);
  if (index >= 0) {
    const current = slot.realtimeMessages[index];
    if (current.turnId === traceMessage.turnId && current.content === traceMessage.content) return false;
    slot.realtimeMessages = [...slot.realtimeMessages];
    slot.realtimeMessages[index] = { ...current, ...traceMessage, created_at: current.created_at || traceMessage.created_at };
    return true;
  }
  const streamingIndex = slot.realtimeMessages.findIndex((item) => (
    item.id === streamId
    && canonicalMessageTurnId(item, existingAliasMap) === canonicalTraceTurnId
  ));
  if (streamingIndex >= 0) {
    const current = slot.realtimeMessages[streamingIndex];
    slot.realtimeMessages = slot.realtimeMessages.filter((item, itemIndex) => (
      itemIndex === streamingIndex
      || !(
        item.turnId === turnId
        && item.role === 'assistant'
        && (item.id === traceId || item.id === streamId)
      )
    ));
    const nextIndex = slot.realtimeMessages.findIndex((item) => item === current);
    slot.realtimeMessages[nextIndex] = {
      ...current,
      id: traceId,
      isStreaming: false,
      created_at: current.created_at || traceMessage.created_at,
    };
    return true;
  }
  slot.realtimeMessages = [
    ...slot.realtimeMessages.filter((item) => item.id !== streamId || item.turnId !== turnId),
    traceMessage,
  ];
  return true;
}

/** 读取消息上显式声明的 turn id（兼容驼峰与下划线字段）。 */
export function explicitMessageTurnId(messageItem: ChatMessage): string | undefined {
  const camelTurnId = typeof messageItem.turnId === 'string' ? messageItem.turnId.trim() : '';
  if (camelTurnId) return camelTurnId;
  const snakeTurnId = typeof messageItem.turn_id === 'string' ? messageItem.turn_id.trim() : '';
  return snakeTurnId || undefined;
}

/** 返回消息的有效 turn id，用户消息缺省时使用其自身 id。 */
export function effectiveMessageTurnId(messageItem: ChatMessage): string | undefined {
  return explicitMessageTurnId(messageItem) || (messageItem.role === 'user' ? messageItem.id : undefined);
}

/** 从流式事件数据中读取 turn id，缺失时回退到给定值。 */
export function explicitStreamTurnId(data: Record<string, unknown>, fallbackTurnId: string): string {
  const turnId = typeof data.turn_id === 'string' ? data.turn_id.trim() : '';
  if (turnId) return turnId;
  const userMessageId = typeof data.user_message_id === 'string' ? data.user_message_id.trim() : '';
  if (userMessageId) return userMessageId;
  return fallbackTurnId;
}

/** 解析会话事件所属的 turn id，无法判定时返回空串。 */
export function eventTraceTurnId(event: ChatSessionEventRead): string {
  const data = isPlainRecord(event.data) ? event.data : {};
  const explicit = explicitStreamTurnId(data, '');
  if (explicit) return explicit;
  if (event.event === 'user_message_received') {
    return typeof data.message_id === 'string' ? data.message_id.trim() : '';
  }
  return '';
}

/** 将会话持久化事件转换为前端统一处理的流式事件。 */
export function normalizeSessionEventForStream(event: ChatSessionEventRead): StreamEvent {
  const data = isPlainRecord(event.data) ? event.data : {};
  if (event.event === 'stream_status') {
    return { event: 'status', data };
  }
  if (event.event === 'router_decision_created') {
    return { event: 'router_decision', data };
  }
  if (event.event === 'assistant_message_created') {
    const content = typeof data.reply === 'string' ? data.reply : '';
    return { event: 'stream_replace', data: { ...data, content } };
  }
  return { event: event.event, data };
}

/** 判断会话事件是否标志本轮结束，助手消息创建视为终止事件。 */
export function isTerminalSessionEvent(
  event: ChatSessionEventRead,
  isTerminalStreamEvent: (event: ChatSessionEventRead) => boolean,
): boolean {
  if (event.event === 'assistant_message_created') return true;
  return isTerminalStreamEvent(event);
}

/** 判断实时流是否已接管该轮次，从而应延迟处理对应的持久化事件。 */
export function shouldDeferPersistedEventToLiveStream(
  eventName: string,
  liveStreamOwnsTurn: boolean,
): boolean {
  if (!liveStreamOwnsTurn) return false;
  if (eventName === 'assistant_message_created') return false;
  return !STREAM_TERMINAL_EVENTS.has(eventName);
}

/** 为服务端消息补齐 turn id，优先使用显式值或实时消息上的对应关系。 */
export function attachTurnIdsToServerMessages(
  serverMessages: ChatMessage[],
  realtimeMessages: ChatMessage[],
): ChatMessage[] {
  const realtimeTurnIdsByServerId = new Map(
    realtimeMessages
      .filter((item) => item.turnId && item.serverMessageId)
      .map((item) => [item.serverMessageId as string, item.turnId as string]),
  );

  return serverMessages.map((messageItem) => {
    const turnId = explicitMessageTurnId(messageItem) || realtimeTurnIdsByServerId.get(messageItem.id);
    if (turnId) return { ...messageItem, turnId };
    if (messageItem.role === 'user') return { ...messageItem, turnId: messageItem.id };
    return messageItem;
  });
}

/**
 * 判断实时消息是否应保留在合并结果中。
 *
 * 排队消息始终保留；流式消息仅在归属当前轮次时保留；其余按服务端去重与时间戳取舍。
 */
function shouldKeepRealtimeMessage(
  messageItem: ChatMessage,
  serverMessages: ChatMessage[],
  latestServerTime: number,
  activeTurnId?: string | null,
): boolean {
  if (messageItem.role === 'user' && messageItem.metadata?.queued === true) return true;
  if (messageItem.isStreaming) {
    const aliasMap = buildTurnAliasMap([...serverMessages, messageItem]);
    const messageTurnId = canonicalMessageTurnId(messageItem, aliasMap);
    const activeCanonicalTurnId = canonicalTurnIdForValue(activeTurnId, aliasMap);
    return !messageTurnId || !activeCanonicalTurnId || messageTurnId === activeCanonicalTurnId;
  }
  if (hasServerMessageForTurn(messageItem, serverMessages)) return false;
  if (messageItem.serverMessageId && serverMessages.some((serverMessage) => serverMessage.id === messageItem.serverMessageId)) {
    return false;
  }
  if (
    messageItem.role === 'assistant'
    && (
      Boolean(normalizeMessageText(messageItem.content))
      || messageItem.isError
      || messageItem.id.startsWith('__trace_')
    )
  ) {
    return true;
  }
  if (activeTurnId) {
    const aliasMap = buildTurnAliasMap([...serverMessages, messageItem]);
    const messageTurnId = canonicalMessageTurnId(messageItem, aliasMap);
    const activeCanonicalTurnId = canonicalTurnIdForValue(activeTurnId, aliasMap);
    if (messageTurnId && activeCanonicalTurnId && messageTurnId === activeCanonicalTurnId) return true;
  }
  if (!latestServerTime) return true;
  return parseMessageTime(messageItem.created_at) > latestServerTime;
}

export { shouldKeepRealtimeMessage, hasServerMessageForTurn, latestUserMessageForTurn, timestampAfterMessage };

/**
 * 合并服务端消息与实时消息，按轮次与角色排序并去重。
 *
 * 同轮次仅保留一条助手消息（按服务端优先、有内容、流式、完成度打分）；
 * 排队消息排在最后。
 */
export function computeMergedMessages(slot: SessionSlot, activeTurnId?: string | null): ChatMessage[] {
  const serverIds = new Set(slot.serverMessages.map((item) => item.id));
  const latestServerTime = Math.max(0, ...slot.serverMessages.map((item) => parseMessageTime(item.created_at)));
  const extras = slot.realtimeMessages.filter((item) => {
    if (serverIds.has(item.id)) return false;
    return shouldKeepRealtimeMessage(item, slot.serverMessages, latestServerTime, activeTurnId);
  });
  const combined = [
    ...slot.serverMessages.map((messageItem, index) => ({ messageItem, index, source: 'server' as const })),
    ...extras.map((messageItem, index) => ({ messageItem, index: slot.serverMessages.length + index, source: 'realtime' as const })),
  ];
  const aliasMap = buildTurnAliasMap(combined.map((entry) => entry.messageItem));
  const turnStarts = new Map<string, number>();
  combined.forEach(({ messageItem }) => {
    if (messageItem.role !== 'user') return;
    const turnId = canonicalMessageTurnId(messageItem, aliasMap);
    if (!turnId) return;
    const createdAt = parseMessageTime(messageItem.created_at);
    const previous = turnStarts.get(turnId);
    if (previous === undefined || createdAt < previous) {
      turnStarts.set(turnId, createdAt);
    }
  });
  combined.forEach(({ messageItem }) => {
    const turnId = canonicalMessageTurnId(messageItem, aliasMap);
    if (!turnId || turnStarts.has(turnId)) return;
    turnStarts.set(turnId, parseMessageTime(messageItem.created_at));
  });
  const roleOrder: Record<ChatMessage['role'], number> = {
    user: 0,
    assistant: 1,
    tool: 2,
    system: 3,
  };

  const sorted = combined
    .sort((left, right) => {
      const leftQueued = left.messageItem.role === 'user' && left.messageItem.metadata?.queued === true;
      const rightQueued = right.messageItem.role === 'user' && right.messageItem.metadata?.queued === true;
      if (leftQueued !== rightQueued) return leftQueued ? 1 : -1;
      const leftTurnId = canonicalMessageTurnId(left.messageItem, aliasMap);
      const rightTurnId = canonicalMessageTurnId(right.messageItem, aliasMap);
      const leftTurnStart = leftTurnId ? turnStarts.get(leftTurnId) : undefined;
      const rightTurnStart = rightTurnId ? turnStarts.get(rightTurnId) : undefined;
      const leftSortTime = leftTurnStart ?? parseMessageTime(left.messageItem.created_at);
      const rightSortTime = rightTurnStart ?? parseMessageTime(right.messageItem.created_at);
      if (leftSortTime !== rightSortTime) return leftSortTime - rightSortTime;
      if (leftTurnId && leftTurnId === rightTurnId && left.messageItem.role !== right.messageItem.role) {
        return (roleOrder[left.messageItem.role] ?? 3) - (roleOrder[right.messageItem.role] ?? 3);
      }
      return (
        parseMessageTime(left.messageItem.created_at) - parseMessageTime(right.messageItem.created_at) ||
        left.index - right.index
      );
    });

  const selectedAssistantByTurn = new Map<string, { messageItem: ChatMessage; index: number; source: 'server' | 'realtime' }>();
  const assistantRank = (entry: { messageItem: ChatMessage; source: 'server' | 'realtime' }) => {
    const content = normalizeMessageText(entry.messageItem.content);
    let rank = 0;
    if (entry.source === 'server') rank += 100;
    if (content) rank += 60;
    if (entry.messageItem.isStreaming && (!activeTurnId || entry.messageItem.turnId === activeTurnId)) rank += 40;
    if (!entry.messageItem.isStreaming) rank += 10;
    return rank;
  };
  sorted.forEach((entry) => {
    if (entry.messageItem.role !== 'assistant') return;
    const turnId = canonicalMessageTurnId(entry.messageItem, aliasMap);
    if (!turnId) return;
    const previous = selectedAssistantByTurn.get(turnId);
    if (!previous || assistantRank(entry) >= assistantRank(previous)) {
      selectedAssistantByTurn.set(turnId, entry);
    }
  });

  return sorted
    .filter((entry) => {
      if (entry.messageItem.role !== 'assistant') return true;
      const turnId = canonicalMessageTurnId(entry.messageItem, aliasMap);
      if (!turnId) return true;
      return selectedAssistantByTurn.get(turnId)?.messageItem === entry.messageItem;
    })
    .map((item) => item.messageItem);
}

/** 将流式阶段映射为对外展示的中文状态文案。 */
function publicStreamPhase(data: Record<string, unknown>): string {
  const phase = typeof data.phase === 'string' ? data.phase : '';
  const text = typeof data.text === 'string' ? data.text : '';
  if (phase === 'error') return text || '请求失败';
  if (phase === 'preparing') return text || '正在整理上下文';
  if (phase === 'scheduled_task_draft') return text || '生成定时任务草案';
  if (isKnowledgeTracePhase(phase)) return text || knowledgeTraceText(data);
  return '正在思考';
}

export { publicStreamPhase };

type RecoverableTraceProgress = {
  id?: string;
  kind?: string;
  text?: string;
  detail?: string | null;
  code?: string | null;
  output?: string | null;
  state?: string;
};

/** 判断执行记录行是否包含足以恢复展示的进度信息。 */
function hasRecoverableTraceProgress(lines: RecoverableTraceProgress[]): boolean {
  return lines.some((line) => {
    if (!line) return false;
    if (line.state && line.state !== 'running') return false;
    if (line.detail || line.code || line.output) return true;
    if (line.kind && line.kind !== 'decision') return true;
    const text = String(line.text || '').trim();
    return Boolean(text);
  });
}

/** 判断会话事件流中是否存在可恢复的进度事件。 */
export function hasRecoverableEventProgress(events: ChatSessionEventRead[]): boolean {
  return events.some((event) => {
    if (event.event === 'memory_recalled') return false;
    if (event.event === 'router_decision_created') {
      const data = isPlainRecord(event.data) ? event.data : {};
      const intent = typeof data.user_intent === 'string' ? data.user_intent.trim() : '';
      const reason = typeof data.reason === 'string' ? data.reason.trim() : '';
      const decision = typeof data.decision === 'string' ? data.decision.trim() : '';
      return Boolean(intent || reason || decision);
    }
    return true;
  });
}

/**
 * 判断运行中的执行记录是否可恢复展示。
 *
 * 仅当记录未完成、开始时间有效且在恢复窗口内、并存在可恢复进度行时返回 true。
 */
export function isRecoverableRunningTrace(row: { completed_at?: string | null; lines: RecoverableTraceProgress[]; started_at: string }): boolean {
  if (row.completed_at) return false;
  const startedAt = parseMessageTime(row.started_at);
  if (startedAt <= 0) return false;
  if (Date.now() - startedAt > CHAT_TRACE_RECOVERY_WINDOW_MS) return false;
  const lines = row.lines || [];
  return hasRecoverableTraceProgress(lines);
}

const KNOWLEDGE_TRACE_PHASES = new Set([
  'knowledge',
  'okf_route',
  'okf_only',
  'document_route',
  'document_route_lexical',
  'bucket_route',
  'bucket_route_lexical',
  'section_expand',
  'read_chunks',
  'evidence_pack',
  'no_visible_knowledge',
  'no_documents',
  'no_buckets',
]);

/** 判断给定阶段是否属于知识库检索链路阶段。 */
export function isKnowledgeTracePhase(phase: string): boolean {
  return KNOWLEDGE_TRACE_PHASES.has(phase);
}

/** 返回知识检索阶段文案，缺失时使用「检索知识库」。 */
export function knowledgeTraceText(data: Record<string, unknown>): string {
  const raw = typeof data.message === 'string'
    ? data.message
    : typeof data.text === 'string'
      ? data.text
      : '';
  if (!raw) return '检索知识库';
  return raw;
}

/** 生成知识检索执行记录行的稳定 id，包含规范化后的查询文本。 */
export function knowledgeTraceLineId(data: Record<string, unknown>): string {
  const rawQuery = isPlainRecord(data.query) && typeof data.query.query === 'string'
    ? data.query.query
    : typeof data.query === 'string'
      ? data.query
      : '';
  const query = rawQuery.trim().replace(/\s+/g, ' ');
  return query ? `knowledge_lookup_${query}` : 'knowledge_lookup';
}

/** 汇总知识检索阶段的查询、命中与读取数量作为执行记录详情。 */
export function knowledgeTraceDetail(data: Record<string, unknown>): string | undefined {
  const query = isPlainRecord(data.query) && typeof data.query.query === 'string' ? data.query.query : '';
  const parts = [
    query ? `查询：${query}` : '',
    typeof data.selected_count === 'number' ? `命中知识图谱 ${data.selected_count} 个` : '',
    typeof data.candidate_count === 'number' ? `候选 ${data.candidate_count} 个` : '',
    typeof data.chunk_count === 'number' ? `读取 ${data.chunk_count} 个片段` : '',
    typeof data.evidence_count === 'number' ? `整理 ${data.evidence_count} 条证据` : '',
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : undefined;
}

/** 汇总知识检索结果的命中概念、片段与引用候选数量。 */
export function knowledgeResultTraceDetail(data: Record<string, unknown>): string | undefined {
  const concepts = Array.isArray(data.selected_concepts) ? data.selected_concepts.length : 0;
  const chunks = Array.isArray(data.chunks) ? data.chunks.length : 0;
  const evidence = Array.isArray(data.evidence_pack) ? data.evidence_pack.length : 0;
  const parts = [
    concepts ? `命中知识图谱 ${concepts} 个` : '',
    chunks ? `读取 ${chunks} 个片段` : '',
    evidence ? `生成 ${evidence} 条引用候选` : '',
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : undefined;
}

/** 将未知值规范化为执行记录中的 SOP 信息，缺少 skillId 时返回 null。 */
export function normalizeTraceSkill(value: unknown): TraceSkill | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  const skillId = typeof item.skillId === 'string' ? item.skillId : '';
  if (!skillId) return null;
  return {
    skillId,
    name: typeof item.name === 'string' ? item.name : skillId,
    stepId: typeof item.stepId === 'string' ? item.stepId : undefined,
    state: typeof item.state === 'string' ? item.state : undefined,
  };
}

/** 根据运行时决策与 SOP 状态生成执行记录中的 SOP 动作文案。 */
export function streamSkillLabel(data: Record<string, unknown>, skill: TraceSkill): string {
  if (skill.state === 'suspended') return '挂起SOP';
  if (skill.state === 'pending') return '等待SOP';
  const decision = typeof data.runtimeDecision === 'string' ? data.runtimeDecision : '';
  const fromSkillId = typeof data.fromSkillId === 'string' ? data.fromSkillId : '';
  const toSkillId = typeof data.toSkillId === 'string' ? data.toSkillId : '';
  if (decision === 'start_skill' || decision === 'start_new_task') return '选择SOP';
  if (decision === 'suspend_current_and_start_new_skill') return '切换SOP';
  if (
    (decision === 'answer_related_question_then_resume' || decision === 'answer_chitchat_then_resume')
    && fromSkillId
    && toSkillId
    && fromSkillId !== toSkillId
  ) return '切换SOP';
  if (decision === 'exit_current_skill') return '恢复SOP';
  return '推进SOP';
}

/** 将未知值规范化为执行记录中的工具信息，缺少 toolId 时返回 null。 */
export function normalizeTraceTool(value: unknown): TraceTool | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  const toolId = typeof item.toolId === 'string' ? item.toolId : '';
  if (!toolId) return null;
  return {
    toolId,
    toolCallId: typeof item.toolCallId === 'string' ? item.toolCallId : undefined,
    toolName: typeof item.toolName === 'string' ? item.toolName : toolId,
    rawToolName: typeof item.rawToolName === 'string' ? item.rawToolName : toolId,
    success: typeof item.success === 'boolean' ? item.success : undefined,
    isError: typeof item.isError === 'boolean' ? item.isError : undefined,
    content: item.content,
  };
}

/** 将字符串或数字、布尔值转为简短展示文本，其它类型返回空串。 */
function shortTraceValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

/** 汇总工具调用来源、命中情况与建议，作为执行记录详情。 */
export function toolTraceDetail(tool: TraceTool): string | undefined {
  const content = tool.content && typeof tool.content === 'object' ? tool.content as Record<string, unknown> : null;
  const data = content?.data && typeof content.data === 'object' ? content.data as Record<string, unknown> : null;
  const parts = [
    tool.rawToolName && tool.rawToolName !== tool.toolName ? tool.rawToolName : '',
    shortTraceValue(data?.source),
    data?.found === false ? '未命中' : data?.found === true ? '已命中' : '',
    shortTraceValue(data?.miss_reason),
    shortTraceValue(data?.recommendation),
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(' · ') : undefined;
}

/** 汇总反思原因与目标工具、SOP、步骤，作为执行记录详情。 */
export function reflectionTraceDetail(data: Record<string, unknown>): string | undefined {
  const parts = [
    typeof data.reason === 'string' ? data.reason : '',
    typeof data.target_tool_name === 'string' ? `工具 ${data.target_tool_name}` : '',
    typeof data.target_skill_id === 'string' ? `SOP ${data.target_skill_id}` : '',
    typeof data.target_step_id === 'string' ? `步骤 ${data.target_step_id}` : '',
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(' · ') : undefined;
}

/** 生成流式错误的简短文案，按错误码与事件类型区分。 */
function streamErrorText(data: Record<string, unknown>, eventName: string): string {
  const code = typeof data.code === 'string' ? data.code.trim() : '';
  if (code === 'LLM_ERROR') return '模型调用失败';
  if (eventName === 'stream_interrupted') return '响应生成中断';
  if (code) return `执行失败 ${code}`;
  const errorType = typeof data.error_type === 'string' ? data.error_type.trim() : '';
  return errorType ? `执行失败 ${errorType}` : '执行失败';
}

/** 生成流式错误详情，去重后拼接并截断到 2000 字符。 */
function streamErrorDetail(data: Record<string, unknown>): string | undefined {
  const parts = [
    typeof data.code === 'string' ? data.code.trim() : '',
    typeof data.error_type === 'string' ? data.error_type.trim() : '',
    typeof data.message === 'string' ? data.message.trim() : '',
    typeof data.reason === 'string' ? data.reason.trim() : '',
    typeof data.text === 'string' ? data.text.trim() : '',
  ].filter(Boolean);
  const deduped = parts.filter((part, index) => parts.indexOf(part) === index);
  return deduped.length > 0 ? deduped.join(' · ').slice(0, 2000) : undefined;
}

/** 将流式错误事件转换为失败状态的执行记录行。 */
export function streamErrorTraceLine(data: Record<string, unknown>, eventName: string): TraceLine {
  const code = typeof data.code === 'string' ? data.code.trim() : '';
  const errorType = typeof data.error_type === 'string' ? data.error_type.trim() : '';
  const key = code || errorType || eventName || 'error';
  return {
    id: eventName === 'stream_interrupted' ? 'generation_interrupted' : `error_${key}`,
    kind: 'decision',
    text: streamErrorText(data, eventName),
    detail: streamErrorDetail(data),
    state: 'failed',
    icon: 'loading',
  };
}

/** 将路由决策事件转换为执行记录行，展示识别到的意图与目标。 */
export function routerDecisionTraceLine(data: Record<string, unknown>): TraceLine {
  const intent = typeof data.user_intent === 'string' ? data.user_intent.trim() : '';
  const decision = typeof data.decision === 'string' ? data.decision.trim() : '';
  const skillId = typeof data.target_skill_id === 'string' ? data.target_skill_id.trim() : '';
  const stepId = typeof data.target_step_id === 'string' ? data.target_step_id.trim() : '';
  const reason = typeof data.reason === 'string' ? data.reason.trim() : '';
  const detail = [reason, skillId ? `目标SOP ${skillId}` : '', stepId ? `目标节点 ${stepId}` : '']
    .filter(Boolean)
    .join(' · ');
  return {
    id: 'decision_router',
    kind: 'decision',
    text: intent ? `判断意图 ${intent}` : decision ? `判断意图 ${decision}` : '判断意图',
    detail: detail || undefined,
    state: 'completed',
    icon: 'judge',
  };
}

/** 将步骤结果事件转换为执行记录行，区分工具调用、知识查询与普通推进。 */
export function stepResultTraceLine(data: Record<string, unknown>): TraceLine {
  const toolCall = isPlainRecord(data.tool_call) ? data.tool_call : undefined;
  const knowledgeQuery = isPlainRecord(data.knowledge_query) ? data.knowledge_query : undefined;
  const nextStepId = typeof data.next_step_id === 'string' ? data.next_step_id.trim() : '';
  const reply = typeof data.reply === 'string' ? data.reply.trim() : '';
  const toolName = typeof toolCall?.name === 'string' ? toolCall.name.trim() : '';
  const knowledgeQueryText = typeof knowledgeQuery?.query === 'string' ? knowledgeQuery.query.trim() : '';
  const detail = [
    nextStepId ? `下一节点 ${nextStepId}` : '',
    knowledgeQueryText ? `查询：${knowledgeQueryText}` : '',
    !toolName && !knowledgeQueryText && reply ? reply.slice(0, 80) : '',
  ].filter(Boolean).join(' · ');

  if (toolName) {
    return {
      id: `decision_step_tool_${toolName}`,
      kind: 'decision',
      text: `决定调用工具 ${toolName}`,
      detail: detail || undefined,
      state: 'running',
      icon: 'tool',
    };
  }
  if (knowledgeQueryText) {
    return {
      id: 'decision_step_knowledge',
      kind: 'decision',
      text: '决定查询知识库',
      detail: detail || undefined,
      state: 'running',
      icon: 'advance',
    };
  }
  return {
    id: 'decision_step_result',
    kind: 'decision',
    text: nextStepId ? '决定下一步' : '完成步骤判断',
    detail: detail || undefined,
    state: 'completed',
    icon: 'advance',
  };
}

/**
 * 合并同一执行记录行的新旧数据。
 *
 * 已结束的状态不会被新到的 running 覆盖；临时行不覆盖已有正文与详情。
 */
export function mergeTraceLine(existing: TraceLine, incoming: TraceLine): TraceLine {
  const keepExistingContent = incoming.provisional === true && existing.provisional !== true;
  const nextState =
    existing.state !== 'running' && incoming.state === 'running'
      ? existing.state
      : incoming.state;
  return {
    ...existing,
    ...incoming,
    text: keepExistingContent ? existing.text : incoming.text || existing.text,
    detail: keepExistingContent ? existing.detail : incoming.detail ?? existing.detail,
    code: incoming.code ?? existing.code,
    language: incoming.language ?? existing.language,
    output: incoming.output ?? existing.output,
    outputLanguage: incoming.outputLanguage ?? existing.outputLanguage,
    outputTitle: incoming.outputTitle ?? existing.outputTitle,
    state: nextState,
    provisional: incoming.provisional === true && existing.provisional === true,
  };
}

/**
 * 合并同一轮次的执行记录快照。
 *
 * 按行 id 合并；未完成时保留旧快照中未出现的非占位行；最多保留最后 80 行。
 */
export function mergeTurnTraceSnapshot(existing: TurnTrace | undefined, incoming: TurnTrace): TurnTrace {
  if (!existing) return incoming;

  const existingById = new Map(existing.lines.map((line) => [line.id, line]));
  const incomingIds = new Set(incoming.lines.map((line) => line.id));
  const mergedLines = incoming.lines.map((line) => {
    const previous = existingById.get(line.id);
    return previous ? mergeTraceLine(previous, line) : line;
  });

  const incomingStillRunning = !incoming.completedAt;
  if (incomingStillRunning) {
    existing.lines.forEach((line) => {
      if (!incomingIds.has(line.id) && !line.placeholder) {
        mergedLines.push(line);
      }
    });
  }

  const startedAt = existing.startedAt > 0 && incoming.startedAt > 0
    ? Math.min(existing.startedAt, incoming.startedAt)
    : existing.startedAt || incoming.startedAt;

  return {
    lines: mergedLines.slice(-80),
    startedAt,
    completedAt: incoming.completedAt || existing.completedAt,
  };
}

/** 将执行记录载荷格式化为两空格缩进的可读文本，空值返回空串。 */
export function formatTracePayload(value: unknown): string {
  if (value === undefined || value === null || value === '') return '';
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2);
}

/** 判断值是否为非数组的普通对象。 */
export function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

/** 判断执行记录载荷语言：可解析为 JSON 时返回 json，否则返回 text。 */
function tracePayloadLanguage(value: string): string {
  if (!value.trim()) return 'text';
  try {
    JSON.parse(value);
    return 'json';
  } catch {
    return 'text';
  }
}


/**
 * 将 Harness 事件转换为执行记录行。
 *
 * 支持任务框架开始/结束、步骤超时、动作创建、MCP App 展示与工具完成；
 * 其它事件返回 null 由调用方忽略。
 */
export function harnessEventTraceLine(
  eventName: string,
  data: Record<string, unknown>,
): TraceLine | null {
  const frameId = typeof data.task_frame_id === 'string' && data.task_frame_id.trim()
    ? data.task_frame_id.trim()
    : 'current';
  const iteration = typeof data.iteration === 'number' || typeof data.iteration === 'string'
    ? String(data.iteration)
    : '';
  const toolName = typeof data.tool_name === 'string' ? data.tool_name.trim() : '';

  if (eventName === 'task_frame_started') {
    const kind = typeof data.kind === 'string' ? data.kind : 'conversation';
    const skillName = typeof data.skill_name === 'string' && data.skill_name.trim()
      ? data.skill_name.trim()
      : (typeof data.skill_id === 'string' ? data.skill_id.trim() : '');
    const stepId = typeof data.step_id === 'string' ? data.step_id.trim() : '';
    return {
      id: `harness_frame_${frameId}`,
      kind: kind === 'sop' ? 'skill' : 'decision',
      text: kind === 'sop' && skillName ? `开始SOP ${skillName}` : '开始执行任务',
      detail: [
        kind === 'sop' ? 'SOP TaskFrame' : '对话 TaskFrame',
        stepId ? `步骤 ${stepId}` : '',
        typeof data.step_timeout_seconds === 'number'
          ? `单步上限 ${data.step_timeout_seconds} 秒`
          : '',
        typeof data.harness_max_actions === 'number'
          ? `Harness 最多 ${data.harness_max_actions} 轮`
          : '',
      ]
        .filter(Boolean)
        .join(' · '),
      state: 'running',
      icon: kind === 'sop' ? 'advance' : 'execute',
    };
  }
  if (eventName === 'harness_step_timeout') {
    const timeoutSeconds = typeof data.timeout_seconds === 'number' ? data.timeout_seconds : undefined;
    const actionCount = typeof data.action_count === 'number' ? data.action_count : undefined;
    return {
      id: `harness_timeout_${frameId}`,
      kind: 'skill',
      text: 'SOP 单步运行超时',
      detail: [
        timeoutSeconds === undefined ? '' : `上限 ${timeoutSeconds} 秒`,
        actionCount === undefined ? '' : `已执行 ${actionCount} 个动作`,
      ].filter(Boolean).join(' · '),
      state: 'failed',
      icon: 'loading',
    };
  }
  if (eventName === 'task_frame_finished') {
    const kind = typeof data.kind === 'string' ? data.kind : 'conversation';
    const skillName = typeof data.skill_name === 'string' && data.skill_name.trim()
      ? data.skill_name.trim()
      : (typeof data.skill_id === 'string' ? data.skill_id.trim() : '');
    const stepId = typeof data.step_id === 'string' ? data.step_id.trim() : '';
    const status = typeof data.status === 'string' ? data.status : 'completed';
    const failed = ['failed', 'blocked', 'cancelled'].includes(status);
    const actionCount = typeof data.action_count === 'number' ? data.action_count : undefined;
    const text = kind === 'sop' && skillName
      ? (failed
        ? `SOP执行失败 ${skillName}`
        : (status === 'awaiting_user'
          ? `等待用户补充 ${skillName}`
          : `SOP任务执行完成 ${skillName}`))
      : (failed ? '任务执行失败' : '任务执行完成');
    return {
      id: `harness_frame_${frameId}`,
      kind: kind === 'sop' ? 'skill' : 'decision',
      text,
      detail: [
        `状态 ${status}`,
        stepId ? `步骤 ${stepId}` : '',
        actionCount === undefined ? '' : `执行 ${actionCount} 个动作`,
      ]
        .filter(Boolean)
        .join(' · '),
      state: failed ? 'failed' : (status === 'awaiting_user' ? 'running' : 'completed'),
      icon: failed ? 'loading' : 'execute',
    };
  }
  if (eventName === 'harness_action_created') {
    const action = typeof data.action === 'string' ? data.action : '';
    if (action === 'tool') {
      return {
        id: `harness_action_${frameId}_${iteration || 'current'}`,
        kind: 'tool',
        text: toolName ? `调用能力 ${toolName}` : '调用能力',
        detail: iteration ? `第 ${iteration} 个动作` : undefined,
        state: 'running',
        icon: 'tool',
      };
    }
    if (action === 'finish') {
      return {
        id: `harness_finish_${frameId}_${iteration || 'current'}`,
        kind: 'decision',
        text: '整理任务结果',
        detail: iteration ? `第 ${iteration} 个动作` : undefined,
        state: 'completed',
        icon: 'advance',
      };
    }
    return null;
  }
  if (eventName === 'harness_mcp_app_view') {
    const mcpApp = isPlainRecord(data.mcp_app)
      ? data.mcp_app as TraceLine['mcpApp']
      : undefined;
    if (!mcpApp) return null;
    const appToolName = typeof data.tool_name === 'string' ? data.tool_name : mcpApp.tool_name;
    return {
      id: `harness_mcp_app_${frameId}_${appToolName || 'view'}`,
      kind: 'tool',
      text: appToolName ? `展示 MCP App ${appToolName}` : '展示 MCP App',
      detail: '隔离视图；加载失败时保留文本结果',
      mcpApp,
      state: 'completed',
      icon: 'tool',
    };
  }
  if (eventName === 'harness_tool_completed') {
    const success = data.success === true;
    const result = isPlainRecord(data.result) ? data.result : {};
    const mcpApp = isPlainRecord(result.mcp_app)
      ? result.mcp_app as TraceLine['mcpApp']
      : undefined;
    const error = isPlainRecord(data.error) ? data.error : {};
    const detail = [
      typeof error.code === 'string' ? error.code : '',
      typeof error.message === 'string' ? error.message : '',
    ].filter(Boolean).join(' · ') || undefined;
    const output = formatTracePayload(data.result);
    return {
      id: `harness_action_${frameId}_${iteration || 'current'}`,
      kind: 'tool',
      text: toolName
        ? `${success ? '能力调用完成' : '能力调用失败'} ${toolName}`
        : success ? '能力调用完成' : '能力调用失败',
      detail,
      output: output || undefined,
      outputLanguage: output ? tracePayloadLanguage(output) : undefined,
      outputTitle: output ? '查看能力结果' : undefined,
      collapsible: Boolean(output),
      mcpApp,
      state: success ? 'completed' : 'failed',
      icon: 'tool',
    };
  }
  return null;
}


/** 生成通用技能阶段的执行记录详情，反思阶段优先展示原因与修复建议。 */
export function generalSkillTraceDetail(data: Record<string, unknown>, phase: string): string | undefined {
  const review = isPlainRecord(data.review) ? data.review : undefined;
  if (phase.startsWith('reflection_')) {
    return [
      typeof review?.reason === 'string' ? review.reason : '',
      typeof review?.repair_hint === 'string' ? review.repair_hint : '',
    ]
      .filter(Boolean)
      .join(' · ') || undefined;
  }
  const detail = typeof data.rationale === 'string'
    ? data.rationale
    : typeof data.text === 'string'
      ? data.text
      : undefined;
  return detail?.trim() || undefined;
}

/**
 * 生成通用技能阶段的折叠输出内容。
 *
 * 按阶段返回 stdout/stderr 预览、代码执行结果或校验详情；无内容时返回空对象。
 */
export function generalSkillTraceOutput(data: Record<string, unknown>, phase: string, accumulatedText?: string): {
  output?: string;
  language?: string;
  title?: string;
} {
  if (phase === 'stdout_chunk') {
    const output = formatTracePayload(accumulatedText || data.stdout_preview || data.text);
    return output ? { output, language: tracePayloadLanguage(output), title: '查看运行输出' } : {};
  }
  if (phase === 'stderr_chunk') {
    const output = formatTracePayload(accumulatedText || data.stderr_preview || data.text);
    return output ? { output, language: tracePayloadLanguage(output), title: '查看错误输出' } : {};
  }
  if (phase === 'code_finished' || phase === 'code_timeout') {
    const result: Record<string, unknown> = {};
    if ('return_code' in data) result.return_code = data.return_code;
    if ('structured_result' in data) result.structured_result = data.structured_result;
    if (typeof data.stdout_preview === 'string' && data.stdout_preview.trim()) result.stdout = data.stdout_preview;
    if (typeof data.stderr_preview === 'string' && data.stderr_preview.trim()) result.stderr = data.stderr_preview;
    const output = Object.keys(result).length > 0
      ? formatTracePayload(result)
      : formatTracePayload(data.stdout_preview || data.stderr_preview || data.text);
    return output ? { output, language: tracePayloadLanguage(output), title: phase === 'code_timeout' ? '查看超时结果' : '查看执行结果' } : {};
  }
  if (phase.startsWith('reflection_')) {
    const result: Record<string, unknown> = {};
    if ('structured_result' in data) result.structured_result = data.structured_result;
    if ('review' in data) result.review = data.review;
    if (typeof data.stdout_preview === 'string' && data.stdout_preview.trim()) result.stdout = data.stdout_preview;
    if (typeof data.stderr_preview === 'string' && data.stderr_preview.trim()) result.stderr = data.stderr_preview;
    const output = Object.keys(result).length > 0 ? formatTracePayload(result) : '';
    return output ? { output, language: tracePayloadLanguage(output), title: '查看校验详情' } : {};
  }
  return {};
}

/** 按 UI 配置判断执行记录行是否可见，失败行始终可见。 */
export function traceLineAllowed(line: TraceLine, config: UIConfigRead): boolean {
  if (line.state === 'failed') return true;
  if (line.kind === 'thinking' || line.kind === 'decision') return config.show_thinking_trace;
  if (line.kind === 'code') return config.show_thinking_trace;
  if (line.kind === 'skill') return config.show_skill_trace;
  if (line.kind === 'tool') return config.show_tool_trace;
  return true;
}

/** 汇总执行记录的整体文案与状态（已完成、运行中或失败）。 */
export function traceSummary(trace: TurnTrace, lines: TraceLine[]): { text: string; state: TraceLine['state'] } {
  if (trace.completedAt) {
    if (lines.some((line) => line.state === 'failed')) {
      return { text: '执行遇到问题', state: 'failed' };
    }
    return { text: '执行记录', state: 'completed' };
  }
  if (lines.some((line) => line.state === 'running')) {
    return { text: '执行记录', state: 'running' };
  }
  if (lines.some((line) => line.state === 'failed')) {
    return { text: '执行遇到问题', state: 'failed' };
  }
  return { text: '执行记录', state: 'completed' };
}

/** 过滤出可展开的执行记录行，默认隐藏未失败的思考行。 */
export function traceDetails(lines: TraceLine[]): TraceLine[] {
  const details = lines.filter((line) => {
    if (line.placeholder) return false;
    if (line.kind === 'thinking' && line.state !== 'failed') return false;
    return true;
  });
  return details.length > 0
    ? details
    : lines.filter((line) => !line.placeholder && (line.kind !== 'thinking' || line.state === 'failed'));
}

/** 判断消息是否可评分：仅非流式、非错误的正式助手回复可评分。 */
export function canRateMessage(item: ChatMessage): boolean {
  return (
    item.role === 'assistant'
    && !item.isStreaming
    && !item.isError
    && !item.id.startsWith('__')
    && !item.id.startsWith('text_')
    && !item.id.startsWith('error_')
  );
}

/** 移除正文末尾的参考来源标签行与引用清单段落。 */
export function stripTrailingCitationSummary(content: string): string {
  const citationHeading = '(?:参考来源|参考资料|引用来源|资料来源)';
  const labelFooter = new RegExp(
    `(?:^|\\n)\\s*${citationHeading}\\s*[:：]\\s*(?:\\[\\d+\\]\\s*)+\\s*$`,
    'u',
  );
  const citationSection = new RegExp(
    `(?:^|\\n)\\s{0,3}(?:#{1,6}\\s*)?${citationHeading}\\s*[:：]?\\s*`
      + `(?:\\n\\s*(?:[-*+]\\s+|\\d+[.)]\\s+)?\\[\\d+\\][^\\n]*)+\\s*$`,
    'u',
  );

  let stripped = content.trimEnd();
  let previous = '';
  while (stripped !== previous) {
    previous = stripped;
    stripped = stripped.replace(labelFooter, '').trimEnd();
    stripped = stripped.replace(citationSection, '').trimEnd();
  }
  return stripped;
}

/** 收集正文中出现的引用编号（形如 [1]）。 */
function citationLabelsInContent(content: string): Set<number> {
  const labels = new Set<number>();
  content.replace(/\[(\d+)\]/g, (_match, value: string) => {
    const label = Number(value);
    if (Number.isInteger(label) && label >= 1) {
      labels.add(label);
    }
    return _match;
  });
  return labels;
}

/** 读取引用标签中的编号，无法解析时返回给定兜底值。 */
function citationLabelNumber(citation: KnowledgeCitation, fallback: number): number {
  const labelText = citation.label || citation.id;
  const match = String(labelText || '').match(/\[(\d+)\]/);
  if (match) {
    const label = Number(match[1]);
    if (Number.isInteger(label) && label >= 1) {
      return label;
    }
  }
  return fallback;
}

/**
 * 提取消息中实际被正文引用的知识引用，并按编号去重排序。
 *
 * 正文存在引用编号时只保留被引用的项；优先按 chunk/concept 标识去重以保留同文档多片段。
 */
export function knowledgeCitations(item: ChatMessage, content: string): KnowledgeCitation[] {
  const citations = item.metadata?.knowledge_citations;
  if (!Array.isArray(citations)) return [];
  const usedLabels = citationLabelsInContent(content);
  const seen = new Set<string>();
  const result: KnowledgeCitation[] = [];
  citations.forEach((citation, index) => {
    if (!citation || !citation.id) return;
    const labelNumber = citationLabelNumber(citation, index + 1);
    if (usedLabels.size > 0 && !usedLabels.has(labelNumber)) return;
    // A document can contribute multiple cited chunks with the same display
    // title. Prefer durable source identifiers so those cards are not merged.
    // Historical citations without source identifiers retain title-based
    // deduplication for backwards compatibility.
    const identity = citation.chunk_id
      ? `chunk:${citation.chunk_id}`
      : citation.concept_id
        ? `concept:${citation.concept_id}`
        : (citation.title || citation.section_path || citation.summary || citation.excerpt || citation.source_path || citation.id);
    const key = normalizeMessageText(identity).toLowerCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    result.push({ ...citation, label: `[${labelNumber}]` });
  });
  return result.sort((a, b) => citationLabelNumber(a, 0) - citationLabelNumber(b, 0));
}

/** 读取消息上的定时任务草案，字段不完整或标记不创建时返回 null。 */
export function scheduledDraftForMessage(item: ChatMessage): ScheduledTaskDraftRead | null {
  const draft = item.metadata?.scheduled_task_draft;
  if (!isPlainRecord(draft) || draft.should_create === false) return null;
  if (typeof draft.title !== 'string' || typeof draft.prompt !== 'string' || typeof draft.agent_id !== 'string') {
    return null;
  }
  return draft as unknown as ScheduledTaskDraftRead;
}

/** 读取消息上已创建的定时任务，字段不完整时返回 undefined。 */
export function createdScheduledTaskForMessage(item: ChatMessage): ScheduledTaskRead | undefined {
  const task = item.metadata?.scheduled_task_created;
  if (!isPlainRecord(task)) return undefined;
  if (typeof task.id !== 'string' || typeof task.title !== 'string' || typeof task.prompt !== 'string') {
    return undefined;
  }
  return task as unknown as ScheduledTaskRead;
}

/** 判断消息是否为定时任务交互模式下的用户指令。 */
export function isScheduledTaskPrompt(item: ChatMessage): boolean {
  return item.role === 'user' && item.metadata?.interaction_mode === 'scheduled_task';
}

/** 返回引用类型的中文标签。 */
export function citationKindLabel(citation: KnowledgeCitation): string {
  if (citation.kind === 'concept') return '知识图谱';
  if (citation.kind === 'okf') return '知识图谱引用';
  return '引用来源';
}

/** 返回引用的展示标题，缺失时回退到章节、来源、概念 id 或「知识引用」。 */
export function citationDisplayTitle(citation: KnowledgeCitation): string {
  const raw = citation.title || citation.section_path || citation.source_path || citation.concept_id || '知识引用';
  return raw.trim() || '知识引用';
}

/** 返回引用的来源路径标签，缺失时返回空串。 */
export function citationSourceLabel(citation: KnowledgeCitation): string {
  const raw = citation.source_path || '';
  if (!raw) return '';
  return raw.trim();
}

/** 返回引用的章节标签，缺失时回退到标题。 */
export function citationSectionLabel(citation: KnowledgeCitation): string {
  const raw = citation.section_path || citation.title || '';
  return raw.trim();
}

// ---------------------------------------------------------------------------
// Clipboard / pasted image helpers
// ---------------------------------------------------------------------------
const MAX_PASTED_REMOTE_IMAGES = 6;

type ClipboardImageItem = {
  types: readonly string[];
  getType: (type: string) => Promise<Blob>;
};

/** 判断剪贴板中是否包含可粘贴到输入框的图片。 */
export function clipboardContainsComposerImage(clipboardData: DataTransfer): boolean {
  if (Array.from(clipboardData.files || []).some((file) => file.type.startsWith('image/'))) {
    return true;
  }
  if (Array.from(clipboardData.items || []).some((item) => item.kind === 'file' && item.type.startsWith('image/'))) {
    return true;
  }
  return extractImageSourceUrls(clipboardData.getData('text/html')).length > 0
    || extractImageSourceUrls(clipboardData.getData('text/plain')).length > 0;
}

/**
 * 从剪贴板中提取待发送的附件文件。
 *
 * 先同步收集文件与内联图片，再按上限拉取远程图片；仍为空时尝试读取剪贴板图片。
 */
export async function extractPastedComposerFiles(clipboardData: DataTransfer): Promise<File[]> {
  const files = extractPastedComposerFilesSync(clipboardData);
  const seen = new Set(files.map(pastedFileKey));

  const pushFile = (file: File | null | undefined) => {
    if (!file || file.size <= 0) return;
    const key = pastedFileKey(file);
    if (seen.has(key)) return;
    seen.add(key);
    files.push(file);
  };

  const imageSources = [
    ...extractImageSourceUrls(clipboardData.getData('text/html')),
    ...extractImageSourceUrls(clipboardData.getData('text/plain')),
  ].filter((source) => !isImageDataUrl(source));

  for (const [index, source] of imageSources.slice(0, MAX_PASTED_REMOTE_IMAGES).entries()) {
    pushFile(await imageSourceToFile(source, files.length + index));
  }

  if (files.length === 0) {
    const clipboardImages = await readClipboardImageItems();
    clipboardImages.forEach((file) => pushFile(file));
  }

  return files;
}

/** 同步收集剪贴板中的文件、图片项与内联 data URL 图片。 */
function extractPastedComposerFilesSync(clipboardData: DataTransfer): File[] {
  const files: File[] = [];
  const seen = new Set<string>();

  const pushFile = (file: File | null | undefined, index: number) => {
    if (!file || file.size <= 0) return;
    const normalized = normalizePastedFile(file, index);
    const key = pastedFileKey(normalized);
    if (seen.has(key)) return;
    seen.add(key);
    files.push(normalized);
  };

  Array.from(clipboardData.files || []).forEach((file, index) => pushFile(file, index));

  Array.from(clipboardData.items || []).forEach((item, index) => {
    if (item.kind !== 'file' || !item.type.startsWith('image/')) return;
    pushFile(item.getAsFile(), files.length + index);
  });

  const dataUrls = [
    ...extractImageDataUrls(clipboardData.getData('text/html')),
    ...extractImageDataUrls(clipboardData.getData('text/plain')),
  ];
  dataUrls.forEach((dataUrl, index) => pushFile(dataUrlToImageFile(dataUrl, index), files.length + index));

  return files;
}

/** 为缺乏有效文件名的粘贴文件生成带扩展名的文件名。 */
function normalizePastedFile(file: File, index: number): File {
  const type = file.type || 'application/octet-stream';
  const hasUsefulName = Boolean(file.name && !/^image\.(png|jpe?g|gif|webp)$/i.test(file.name));
  if (hasUsefulName) return file;

  const filename = type.startsWith('image/')
    ? `pasted-image-${Date.now()}-${index + 1}.${imageExtension(type)}`
    : (file.name || `pasted-file-${Date.now()}-${index + 1}`);
  return new File([file], filename, { type, lastModified: file.lastModified || Date.now() });
}

/** 生成粘贴文件去重键（类型 + 大小）。 */
function pastedFileKey(file: File): string {
  return `${file.type || 'application/octet-stream'}:${file.size}`;
}

/** 从原始文本或 HTML 中提取内联图片 data URL。 */
function extractImageDataUrls(raw: string): string[] {
  return extractImageSourceUrls(raw).filter(isImageDataUrl);
}

/** 从原始文本或 HTML 中提取图片地址（data URL、图片元素 src 与图片链接）。 */
function extractImageSourceUrls(raw: string): string[] {
  if (!raw) return [];
  const urls = new Set<string>();
  const text = raw.trim();
  if (isImageDataUrl(text) || isLikelyImageUrl(text)) {
    urls.add(text);
  }

  try {
    const document = new DOMParser().parseFromString(raw, 'text/html');
    Array.from(document.images).forEach((image) => {
      const src = image.getAttribute('src') || '';
      if (isSupportedPastedImageSource(src, true)) urls.add(src.trim());
    });
  } catch {
    // DOMParser is best-effort here; the regex below still catches inline image data.
  }

  const matches = raw.match(/data:image\/[a-z0-9.+-]+(?:;[a-z0-9.+-]+=[^,;]*)*;base64,[a-z0-9+/=\r\n]+/gi) || [];
  matches.forEach((url) => {
    if (isImageDataUrl(url)) urls.add(url);
  });
  const urlMatches = raw.match(/https?:\/\/[^\s"'<>]+/gi) || [];
  urlMatches.forEach((url) => {
    if (isLikelyImageUrl(url)) urls.add(url);
  });
  return Array.from(urls);
}

/** 判断字符串是否为图片 data URL。 */
function isImageDataUrl(value: string): boolean {
  return /^data:image\/[a-z0-9.+-]+(?:;[^,]*)?,/i.test(value.trim());
}

/** 判断粘贴来源是否可用作图片（data URL、blob、http(s) 或协议相对地址）。 */
function isSupportedPastedImageSource(value: string, fromImageElement = false): boolean {
  const trimmed = value.trim();
  if (!trimmed) return false;
  if (isImageDataUrl(trimmed)) return true;
  if (trimmed.startsWith('blob:')) return true;
  if (/^https?:\/\//i.test(trimmed)) return fromImageElement || isLikelyImageUrl(trimmed);
  if (trimmed.startsWith('//')) return fromImageElement || isLikelyImageUrl(`https:${trimmed}`);
  return false;
}

/** 判断 URL 是否以常见图片扩展名结尾。 */
function isLikelyImageUrl(value: string): boolean {
  return /^https?:\/\/[^\s"'<>]+\.(?:png|jpe?g|gif|webp|bmp|svg|heic|tiff?)(?:[?#][^\s"'<>]*)?$/i.test(value.trim());
}

/**
 * 将远程或内联图片地址下载/解码为 File。
 *
 * 下载失败、响应非图片或地址不受支持时返回 null。
 */
async function imageSourceToFile(source: string, index: number): Promise<File | null> {
  const normalized = normalizePastedImageSource(source);
  if (!normalized) return null;
  if (isImageDataUrl(normalized)) {
    return dataUrlToImageFile(normalized, index);
  }
  try {
    const response = await fetch(normalized);
    if (!response.ok) return null;
    const blob = await response.blob();
    if (!blob.type.startsWith('image/')) return null;
    return blobToPastedImageFile(blob, index, pastedImageNameFromUrl(normalized));
  } catch {
    return null;
  }
}

/** 规范化粘贴图片地址，协议相对地址补全为当前页面协议，不支持时返回 null。 */
function normalizePastedImageSource(source: string): string | null {
  const trimmed = source.trim();
  if (!trimmed) return null;
  if (isImageDataUrl(trimmed) || trimmed.startsWith('blob:') || /^https?:\/\//i.test(trimmed)) return trimmed;
  if (trimmed.startsWith('//')) return `${window.location.protocol}${trimmed}`;
  return null;
}

/** 从图片 URL 中解析可能的文件名，无法解析时返回 undefined。 */
function pastedImageNameFromUrl(source: string): string | undefined {
  try {
    const pathname = new URL(source, window.location.href).pathname;
    const name = decodeURIComponent(pathname.split('/').filter(Boolean).pop() || '');
    return isLikelyImageFilename(name) ? name : undefined;
  } catch {
    return undefined;
  }
}

/** 判断文件名是否以常见图片扩展名结尾。 */
function isLikelyImageFilename(value: string): boolean {
  return /\.(?:png|jpe?g|gif|webp|bmp|svg|heic|tiff?)$/i.test(value);
}

/** 通过 Clipboard API 读取剪贴板图片并转为 File，不可用或失败时返回空数组。 */
async function readClipboardImageItems(): Promise<File[]> {
  const clipboard = navigator.clipboard as (Clipboard & { read?: () => Promise<ClipboardImageItem[]> }) | undefined;
  if (!clipboard?.read) return [];
  try {
    const items = await clipboard.read();
    const files: File[] = [];
    for (const [index, item] of items.entries()) {
      const imageType = item.types.find((type) => type.startsWith('image/'));
      if (!imageType) continue;
      const blob = await item.getType(imageType);
      files.push(blobToPastedImageFile(blob, index));
    }
    return files;
  } catch {
    return [];
  }
}

/** 将图片 data URL 解码为 File，格式非法或解码失败时返回 null。 */
function dataUrlToImageFile(dataUrl: string, index: number): File | null {
  const match = dataUrl.trim().match(/^data:(image\/[a-z0-9.+-]+)((?:;[^,]*)?),(.*)$/i);
  if (!match) return null;
  const type = match[1] || 'image/png';
  const meta = match[2] || '';
  const payload = match[3] || '';

  try {
    const bytes = meta.toLowerCase().includes(';base64')
      ? bytesFromBase64(payload)
      : new TextEncoder().encode(decodeURIComponent(payload));
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
    return new File([buffer], `pasted-image-${Date.now()}-${index + 1}.${imageExtension(type)}`, { type });
  } catch {
    return null;
  }
}

/** 将 Blob 包装为带文件名的图片 File。 */
function blobToPastedImageFile(blob: Blob, index: number, filename?: string): File {
  const type = blob.type || 'image/png';
  return new File([blob], filename || `pasted-image-${Date.now()}-${index + 1}.${imageExtension(type)}`, {
    type,
    lastModified: Date.now(),
  });
}

/** 将 base64 字符串解码为字节数组。 */
function bytesFromBase64(payload: string): Uint8Array {
  const binary = window.atob(payload.replace(/\s/g, ''));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

/** 根据图片 MIME 类型返回文件扩展名，未知类型回退为 png。 */
function imageExtension(contentType: string): string {
  const normalized = contentType.toLowerCase().split(';')[0];
  if (normalized === 'image/jpeg' || normalized === 'image/jpg') return 'jpg';
  if (normalized === 'image/png') return 'png';
  if (normalized === 'image/gif') return 'gif';
  if (normalized === 'image/webp') return 'webp';
  if (normalized === 'image/bmp') return 'bmp';
  if (normalized === 'image/svg+xml') return 'svg';
  if (normalized === 'image/heic') return 'heic';
  if (normalized === 'image/tiff') return 'tiff';
  return 'png';
}

// ---------------------------------------------------------------------------
// Attachments
// ---------------------------------------------------------------------------
/** 移除输入框附件中的上传状态字段，转为请求所需的附件结构。 */
export function toRequestAttachment(attachment: ComposerAttachment): ChatAttachmentRead {
  const { uploadStatus: _uploadStatus, uploadKey: _uploadKey, ...rest } = attachment;
  return rest;
}

/** 读取消息 metadata 中合法的附件列表。 */
export function messageAttachments(messageItem: ChatMessage): ChatAttachmentRead[] {
  const attachments = messageItem.metadata?.attachments;
  if (!Array.isArray(attachments)) return [];
  return attachments.filter(isChatAttachment);
}

/** 读取并规范化消息中的 Harness 工作区产物，按任务框架与路径去重。 */
export function harnessWorkspaceArtifacts(
  messageItem: ChatMessage,
): HarnessWorkspaceArtifact[] {
  const artifacts = messageItem.metadata?.harness_artifacts;
  if (!Array.isArray(artifacts)) return [];
  const seen = new Set<string>();
  const result: HarnessWorkspaceArtifact[] = [];
  artifacts.forEach((value) => {
    if (!value || typeof value !== 'object') return;
    const artifact = value as Partial<HarnessWorkspaceArtifact>;
    if (
      artifact.type !== 'workspace_file'
      || typeof artifact.task_frame_id !== 'string'
      || !artifact.task_frame_id.trim()
      || typeof artifact.path !== 'string'
      || !artifact.path.trim()
    ) {
      return;
    }
    const identity = `${artifact.task_frame_id}\u001f${artifact.path}`;
    if (seen.has(identity)) return;
    seen.add(identity);
    result.push({
      type: 'workspace_file',
      task_frame_id: artifact.task_frame_id,
      path: artifact.path,
      ...(typeof artifact.sandbox_path === 'string'
        ? { sandbox_path: artifact.sandbox_path }
        : {}),
      ...(typeof artifact.sha256 === 'string' ? { sha256: artifact.sha256 } : {}),
      ...(typeof artifact.size === 'number' && Number.isFinite(artifact.size)
        ? { size: artifact.size }
        : {}),
      ...(typeof artifact.display_name === 'string'
        ? { display_name: artifact.display_name }
        : {}),
      ...(typeof artifact.description === 'string'
        ? { description: artifact.description }
        : {}),
      ...(typeof artifact.content_type === 'string'
        ? { content_type: artifact.content_type }
        : {}),
      ...(typeof artifact.operation === 'string'
        ? { operation: artifact.operation }
        : {}),
      ...(typeof artifact.source === 'string'
        ? { source: artifact.source }
        : {}),
    });
  });
  return result;
}

/** 判断值是否具备附件所需的最小字段（id 与 filename）。 */
function isChatAttachment(value: unknown): value is ChatAttachmentRead {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<ChatAttachmentRead>;
  return typeof item.id === 'string' && typeof item.filename === 'string';
}

/** 生成附件类型与大小的展示文案。 */
export function attachmentTypeLabel(attachment: ChatAttachmentRead): string {
  const size = formatAttachmentSize(attachment.size);
  const type = attachment.kind === 'pdf'
    ? 'PDF'
    : attachment.kind === 'image'
      ? '图片'
      : attachment.kind === 'text'
        ? '文本'
        : '文件';
  return `${type}${size ? ` · ${size}` : ''}`;
}

/** 将字节数格式化为 B/KB/MB 文本，非法或非正数返回空串。 */
function formatAttachmentSize(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10 * 1024 ? 1 : 0)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

// ---------------------------------------------------------------------------
// Scheduled task draft schedule helpers
// ---------------------------------------------------------------------------
/**
 * 将定时任务草案的调度配置格式化为中文描述。
 *
 * 按 once/daily/weekly/monthly 分支输出，缺失时间回退为 09:00。
 */
export function formatDraftSchedule(draft: ScheduledTaskDraftRead): string {
  const schedule = draft.schedule || {};
  const scheduleType = normalizeDraftScheduleType(draft.schedule_type);
  if (scheduleType === 'weekly') {
    const weekdays = Array.isArray(schedule.weekdays)
      ? schedule.weekdays.map((item) => DRAFT_WEEKDAY_LABELS[Number(item)]).filter(Boolean).join('、')
      : '周一';
    return `每周 ${weekdays} ${schedule.time || '09:00'}`;
  }
  if (scheduleType === 'monthly') {
    return `每月 ${schedule.day_of_month || 1} 号 ${schedule.time || '09:00'}`;
  }
  if (scheduleType === 'once') {
    const value = String(schedule.run_at || '');
    const formatted = formatClientDateTime(value, '');
    return formatted
      ? `一次性 ${formatted}`
      : '一次性';
  }
  return `每天 ${schedule.time || '09:00'}`;
}

/** 返回调度类型的中文标签，未知类型按每天处理。 */
export function scheduleTypeLabel(type: ScheduledTaskDraftRead['schedule_type']): string {
  return DRAFT_SCHEDULE_TYPE_LABELS[normalizeDraftScheduleType(type)];
}

/** 返回调度配置在编辑框中的初始值（一次性取 run_at，其余取 time）。 */
export function scheduleEditValue(draft: ScheduledTaskDraftRead): string {
  const schedule = draft.schedule || {};
  if (normalizeDraftScheduleType(draft.schedule_type) === 'once') return String(schedule.run_at || '');
  return String(schedule.time || '09:00');
}

/** 依据调度类型将编辑框的值写回调度配置。 */
export function scheduleFromEditValue(draft: ScheduledTaskDraftRead, value: string): Record<string, unknown> {
  if (normalizeDraftScheduleType(draft.schedule_type) === 'once') {
    return { ...(draft.schedule || {}), run_at: value };
  }
  return { ...(draft.schedule || {}), time: value };
}

/** 按目标调度类型裁剪并补全草案的调度配置。 */
export function draftScheduleForType(schedule: Record<string, unknown>, type: DraftScheduleType): Record<string, unknown> {
  const time = String(schedule.time || '09:00');
  if (type === 'once') {
    return { run_at: String(schedule.run_at || '') };
  }
  if (type === 'weekly') {
    return {
      time,
      weekdays: Array.isArray(schedule.weekdays) ? schedule.weekdays : [0],
    };
  }
  if (type === 'monthly') {
    return {
      time,
      day_of_month: schedule.day_of_month || 1,
    };
  }
  return { time };
}

/** 将未知调度类型规范化为受支持类型，缺省为 daily。 */
export function normalizeDraftScheduleType(value: string): DraftScheduleType {
  const scheduleType = value as DraftScheduleType;
  return DRAFT_SCHEDULE_TYPES.has(scheduleType) ? scheduleType : 'daily';
}
