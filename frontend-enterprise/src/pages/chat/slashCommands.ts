import type { ChatSlashCommand } from '@/types';

const PREFIX_PATTERN = /^\/([^\s]*)(?:\s+([^\s]*))?$/i;
const KINDS = new Set<ChatSlashCommand['kind']>(['sop', 'skill', 'tool']);

export type SlashCommandQuery = {
  kind?: ChatSlashCommand['kind'];
  search: string;
};

export type SlashCommandMessage = {
  command: ChatSlashCommand;
  requestText: string;
};

/** 解析斜杠命令输入，返回筛选类型与关键字；输入不符合 `/` 前缀格式时返回 null。 */
export function slashCommandQuery(input: string): SlashCommandQuery | null {
  const match = PREFIX_PATTERN.exec(input);
  if (!match) return null;
  const prefix = (match[1] || '').trim().toLowerCase();
  const kind = KINDS.has(prefix as ChatSlashCommand['kind'])
    ? prefix as ChatSlashCommand['kind']
    : undefined;
  return {
    kind,
    search: (kind ? match[2] || '' : prefix).trim().toLowerCase(),
  };
}

/**
 * 按当前输入筛选可用的斜杠命令候选。
 *
 * - 输入无法解析为 `/` 前缀时返回空数组；`limit` 小于 1 时按 1 处理。
 * - 命中某个 kind 前缀时先按 kind 过滤，再按关键字匹配 kind/target/label/description。
 */
export function matchingSlashCommands(
  input: string,
  commands: ChatSlashCommand[],
  limit = 10,
): ChatSlashCommand[] {
  const query = slashCommandQuery(input);
  if (!query) return [];
  return commands
    .filter((item) => !query.kind || item.kind === query.kind)
    .filter((item) => {
      if (!query.search) return true;
      return [item.kind, item.target, item.label, item.description]
        .some((value) => value.toLowerCase().includes(query.search));
    })
    .slice(0, Math.max(1, limit));
}

/** 返回填入输入框的斜杠命令文本，末尾保留一个用于接续输入的空格。 */
export function selectedSlashCommandText(command: ChatSlashCommand): string {
  return `${command.command} `;
}

/**
 * 从输入框中剥离已选命令前缀，得到用户实际输入的内容。
 *
 * 未选择命令时原样返回；输入不以该前缀开头时也原样返回，避免误截断。
 */
export function slashCommandComposerText(
  input: string,
  command: ChatSlashCommand | null,
): string {
  if (!command) return input;
  const prefix = selectedSlashCommandText(command);
  return input.startsWith(prefix) ? input.slice(prefix.length) : input;
}

/** 把已选命令前缀与输入框内容拼接为完整输入字符串。 */
export function slashCommandInput(command: ChatSlashCommand, composerText: string): string {
  return `${selectedSlashCommandText(command)}${composerText}`;
}

/**
 * 把形如 `/kind target 需求` 的输入解析为待发送的命令消息。
 *
 * - 格式不匹配时返回 null；kind 仅接受 sop/skill/tool（大小写不敏感）。
 * - 命中已注册命令时复用其标签与描述，否则按输入构造临时命令对象。
 * - `requestText` 为 target 之后的需求正文（已 trim，可为空串）。
 */
export function slashCommandMessage(
  input: string,
  commands: ChatSlashCommand[],
): SlashCommandMessage | null {
  const match = /^\/(sop|skill|tool)\s+([^\s]+)(?:\s+([\s\S]*))?$/iu.exec(input.trim());
  if (!match) return null;
  const kind = match[1].toLocaleLowerCase() as ChatSlashCommand['kind'];
  const target = match[2];
  const commandText = `/${kind} ${target}`;
  const command = commands.find((item) => (
    item.kind === kind
    && (item.target.toLocaleLowerCase() === target.toLocaleLowerCase()
      || item.command.toLocaleLowerCase() === commandText.toLocaleLowerCase())
  )) || {
    kind,
    target,
    label: target,
    description: '',
    command: commandText,
  };
  return { command, requestText: (match[3] || '').trim() };
}

/**
 * 计算命令菜单需要滚动到的位置，使目标选项完整落入可视区。
 *
 * - 选项已在可视区内（含 padding 边距）时原样返回当前 scrollTop。
 * - 越界时向上或向下对齐，并把结果限制为不小于 0。
 */
export function slashMenuScrollTop(
  scrollTop: number,
  viewportHeight: number,
  optionTop: number,
  optionHeight: number,
  padding = 6,
): number {
  const visibleTop = scrollTop + padding;
  const visibleBottom = scrollTop + viewportHeight - padding;
  if (optionTop < visibleTop) return Math.max(0, optionTop - padding);
  const optionBottom = optionTop + optionHeight;
  if (optionBottom > visibleBottom) {
    return Math.max(0, optionBottom - viewportHeight + padding);
  }
  return scrollTop;
}

/** 返回命令类型的展示文案：sop 显示为 SOP，skill 为“技能”，其余视为工具。 */
export function slashCommandKindLabel(kind: ChatSlashCommand['kind']): string {
  if (kind === 'sop') return 'SOP';
  if (kind === 'skill') return '技能';
  return '工具';
}
