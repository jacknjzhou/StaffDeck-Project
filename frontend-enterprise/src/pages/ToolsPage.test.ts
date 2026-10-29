import { describe, expect, it } from 'vitest';

import { parseMcpArgs } from './ToolsPage';

/** 验证 MCP 启动参数的解析规则。 */
describe('parseMcpArgs', () => {
  /** 验证单个参数内部的空格会被完整保留，不会被拆分。 */
  it('preserves spaces inside one argument', () => {
    expect(parseMcpArgs('C:\\Program Files\\mcp server\\index.js')).toEqual([
      'C:\\Program Files\\mcp server\\index.js',
    ]);
  });

  /** 验证按非空行拆分为多个参数，并忽略空行。 */
  it('uses one non-empty line per argument', () => {
    expect(parseMcpArgs('-m\nmy_mcp.server\n\n--label=customer support')).toEqual([
      '-m',
      'my_mcp.server',
      '--label=customer support',
    ]);
  });
});
