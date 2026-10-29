// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest';

import {
  ENTERPRISE_AGENT_STORAGE_KEY,
  isTeamScope,
  readEmployeeScope,
  teamIdFromScope,
  toTeamScope,
} from './agent-scope-storage';

/** 每个用例结束后清空本地存储，避免作用域残留。 */
afterEach(() => {
  window.localStorage.clear();
});

/** 团队（team: 前缀）与员工作用域之间的转换与存储读取规则。 */
describe('team agent-scope helpers', () => {
  /** 验证团队 ID 会拼接 team: 前缀，空 ID 则返回空字符串。 */
  it('builds a team scope value with the team: prefix', () => {
    expect(toTeamScope('team-1')).toBe('team:team-1');
    expect(toTeamScope('')).toBe('');
  });

  /** 验证仅 team:<id> 形式被识别为团队作用域，员工 ID 与空值均不识别。 */
  it('detects team scope values and leaves employee ids alone', () => {
    expect(isTeamScope('team:team-1')).toBe(true);
    expect(isTeamScope('agent-1')).toBe(false);
    expect(isTeamScope('team:')).toBe(false);
    expect(isTeamScope('')).toBe(false);
    expect(isTeamScope(null)).toBe(false);
    expect(isTeamScope(undefined)).toBe(false);
  });

  /** 验证从团队作用域中截取团队 ID，并保留 ID 内部的冒号。 */
  it('extracts the team id from a team scope value', () => {
    expect(teamIdFromScope('team:team-1')).toBe('team-1');
    expect(teamIdFromScope('team:team:with:colons')).toBe('team:with:colons');
    expect(teamIdFromScope('agent-1')).toBe('');
    expect(teamIdFromScope(null)).toBe('');
  });

  /** 验证存储值为团队作用域时，读取员工作用域返回空字符串。 */
  it('reads team scopes as an empty employee scope', () => {
    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'team:team-1');
    expect(readEmployeeScope()).toBe('');
  });

  /** 验证存储的普通员工 ID 会原样返回。 */
  it('reads employee scopes as-is', () => {
    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-1');
    expect(readEmployeeScope()).toBe('agent-1');
  });

  /** 验证存储为空时读取员工作用域返回空字符串。 */
  it('reads an empty storage slot as an empty employee scope', () => {
    expect(readEmployeeScope()).toBe('');
  });
});
