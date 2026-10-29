import { describe, expect, it } from 'vitest';

import type { ChatSlashCommand } from '@/types';

import {
  matchingSlashCommands,
  selectedSlashCommandText,
  slashCommandComposerText,
  slashCommandInput,
  slashCommandMessage,
  slashCommandQuery,
  slashMenuScrollTop,
} from './slashCommands';

const commands: ChatSlashCommand[] = [
  {
    kind: 'sop',
    target: 'refund_v1',
    label: '退款流程',
    description: '处理退款',
    command: '/sop refund_v1',
  },
  {
    kind: 'skill',
    target: 'weather',
    label: '天气查询',
    description: '查询实时天气',
    command: '/skill weather',
  },
  {
    kind: 'tool',
    target: 'price_query',
    label: '价格查询',
    description: '查询商品价格',
    command: '/tool price_query',
  },
];

/** 验证斜杠命令查询、筛选与输入文本拼装的纯函数行为。 */
describe('slash command helpers', () => {
  /** 验证斜杠弹出层仅在请求文本开始输入后关闭。 */
  it('opens for slash prefixes and closes after the request text begins', () => {
    expect(slashCommandQuery('/')).toEqual({ kind: undefined, search: '' });
    expect(slashCommandQuery('/sk')).toEqual({ kind: undefined, search: 'sk' });
    expect(slashCommandQuery('/skill wea')).toEqual({ kind: 'skill', search: 'wea' });
    expect(slashCommandQuery('/skill weather 查询北京天气')).toBeNull();
  });

  /** 验证按资源类型与目标文本过滤命令列表。 */
  it('filters by resource kind and target text', () => {
    expect(matchingSlashCommands('/tool', commands).map((item) => item.target)).toEqual([
      'price_query',
    ]);
    expect(matchingSlashCommands('/skill 天气', commands).map((item) => item.target)).toEqual([
      'weather',
    ]);
  });

  /** 验证选中命令后自动追加空格，便于继续输入请求。 */
  it('inserts a command with a trailing space for the task request', () => {
    expect(selectedSlashCommandText(commands[0])).toBe('/sop refund_v1 ');
  });

  /** 验证命令前缀与可编辑的请求文本相互独立。 */
  it('keeps the command token separate from the editable request text', () => {
    const input = slashCommandInput(commands[0], 'refund order 1001');
    expect(input).toBe('/sop refund_v1 refund order 1001');
    expect(slashCommandComposerText(input, commands[0])).toBe('refund order 1001');
    expect(slashCommandComposerText('plain message', commands[0])).toBe('plain message');
  });

  /** 验证从已发送的用户消息中还原选中的命令卡片。 */
  it('restores a selected command card from a sent user message', () => {
    expect(slashCommandMessage('/skill weather 北京天气如何', commands)).toEqual({
      command: commands[1],
      requestText: '北京天气如何',
    });
  });

  /** 验证资源被移除后仍能渲染历史发送的命令消息。 */
  it('keeps rendering a sent command after its resource is removed', () => {
    expect(slashCommandMessage('/tool legacy_tool 执行检查', [])).toEqual({
      command: {
        kind: 'tool',
        target: 'legacy_tool',
        label: 'legacy_tool',
        description: '',
        command: '/tool legacy_tool',
      },
      requestText: '执行检查',
    });
  });

  /** 验证键盘选中项始终滚动进斜杠菜单可视区域。 */
  it('keeps the keyboard-selected option inside the slash menu viewport', () => {
    expect(slashMenuScrollTop(0, 240, 252, 54)).toBe(72);
    expect(slashMenuScrollTop(120, 240, 92, 54)).toBe(86);
    expect(slashMenuScrollTop(120, 240, 180, 54)).toBe(120);
  });
});
