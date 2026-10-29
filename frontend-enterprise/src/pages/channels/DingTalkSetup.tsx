import { useState } from 'react';
import { notify } from '@/components/ui/app-toast';

import { Input } from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { api, TENANT_ID } from '../../api/client';
import type { ChannelBindingRead } from '../../types';
import { StatusBadge } from '../scheduled-tasks/StatusBadge';

const PRIMARY_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] bg-[#18181a] px-5 text-[12px] font-normal text-white hover:bg-[#303030]';
const OUTLINE_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] border-[#e3e7f1] px-5 text-[12px] font-normal text-[#464c5e] hover:bg-[#f6f6f6] hover:text-[#18181a]';

/**
 * 钉钉渠道凭证配置面板。
 *
 * - 已配置 Client ID 时默认展示只读摘要与连接状态，可点击「轮换 Secret」重新进入编辑。
 * - 未配置凭证时直接展示表单，此时 Client ID 可编辑。
 * - 保存成功后清空 Secret 输入并通过 `onChanged` 把最新绑定回传父级。
 */
export default function DingTalkSetup({
  binding,
  onChanged,
}: {
  binding: ChannelBindingRead;
  onChanged: (updated: ChannelBindingRead) => void;
}) {
  const configuredClientId = binding.client_id || String(binding.config_json?.client_id || '');
  const [editing, setEditing] = useState(!configuredClientId);
  const [clientId, setClientId] = useState(configuredClientId);
  const [clientSecret, setClientSecret] = useState('');
  const [saving, setSaving] = useState(false);

  /**
   * 提交钉钉应用凭证并通知父级更新绑定。
   *
   * - 校验 Client ID / Client Secret 非空，缺失时提示并不发请求。
   * - 成功后清空 Secret、退出编辑态并调用 `onChanged`；失败时提示错误信息。
   * - 请求期间 `saving` 为 true，用于禁用保存按钮避免重复提交。
   */
  async function save() {
    if (!clientId.trim() || !clientSecret.trim()) {
      notify.error('请填写完整凭证');
      return;
    }
    setSaving(true);
    try {
      const updated = await api.post<ChannelBindingRead>(
        `/api/enterprise/channels/${binding.id}/dingtalk/credentials`,
        { tenant_id: TENANT_ID, client_id: clientId.trim(), client_secret: clientSecret.trim() },
      );
      setClientSecret('');
      setEditing(false);
      onChanged(updated);
      notify.success('已保存');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存凭证失败');
    } finally {
      setSaving(false);
    }
  }

  if (configuredClientId && !editing) {
    return (
      <div className="flex flex-wrap items-center gap-[10px] rounded-[10px] bg-[#fafbfc] p-[16px]">
        <span className="text-[12px] text-[#464c5e]">凭证已配置</span>
        <span className="text-[12px] text-[#858b9c]">Client ID：{configuredClientId}</span>
        <StatusBadge tone={binding.connected ? 'green' : 'gray'}>
          {binding.connected ? '已连接' : '未连接'}
        </StatusBadge>
        <UIButton
          variant="outline"
          onClick={() => { setClientSecret(''); setEditing(true); }}
          className={OUTLINE_BUTTON_CLASS}
        >
          轮换 Secret
        </UIButton>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-[12px] rounded-[10px] bg-[#fafbfc] p-[16px]">
      <span className="text-[12px] leading-[1.6] text-[#858b9c]">
        凭证获取路径：钉钉开放平台 → 企业内部应用 → 机器人 → Stream 模式。
      </span>
      <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
        Client ID
        <Input value={clientId} disabled={Boolean(configuredClientId)} autoComplete="off" onChange={(e) => setClientId(e.target.value)} className="h-8 rounded-[10px] text-[12px]" />
      </label>
      <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
        Client Secret
        <Input type="password" value={clientSecret} autoComplete="off" onChange={(e) => setClientSecret(e.target.value)} className="h-8 rounded-[10px] text-[12px]" />
      </label>
      <div className="flex justify-end gap-[8px]">
        {configuredClientId && <UIButton variant="outline" onClick={() => setEditing(false)} className={OUTLINE_BUTTON_CLASS}>取消</UIButton>}
        <UIButton onClick={() => void save()} disabled={saving} className={PRIMARY_BUTTON_CLASS}>保存</UIButton>
      </div>
    </div>
  );
}
