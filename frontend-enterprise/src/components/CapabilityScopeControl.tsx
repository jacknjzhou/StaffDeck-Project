import { InfoCircleOutlined } from '@/icons';
import { Switch } from '@/components/ui/switch';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import type { CapabilityScope } from '@/types';

export type CapabilityScopeResourceType = 'tool' | 'skill' | 'sop' | 'knowledge_base';

const SOP_SPECIFIC_SCOPE_DESCRIPTIONS: Record<CapabilityScopeResourceType, string> = {
  tool: '仅限SOP：只有在SOP步骤中指定相关工具时方可调用',
  skill: '仅限SOP：只有在SOP步骤中指定相关技能时方可调用',
  sop: '仅限 SOP：不参与普通意图匹配和斜杠发现，只能被其他 SOP 节点明确调用。',
  knowledge_base: '仅限SOP：只有在SOP步骤中指定相关知识库时方可调用',
};

const SOP_SPECIFIC_INLINE_DESCRIPTIONS: Record<CapabilityScopeResourceType, string> = {
  tool: '仅在 SOP 步骤指定相关工具时可用。',
  skill: '仅在 SOP 步骤指定相关技能时可用。',
  sop: '仅能作为子 SOP 被明确调用，不会自动触发。',
  knowledge_base: '仅在 SOP 步骤指定相关知识库时可用。',
};

/** 归一化能力范围入参：仅 'sop_specific'（含旧写法 'sop-specific'）视为仅限 SOP，其余回落为通用。 */
export function normalizeCapabilityScope(value: unknown): CapabilityScope {
  return value === 'sop_specific' || value === 'sop-specific' ? 'sop_specific' : 'general';
}

/** 能力范围中文文案：仅限 SOP 或通用。 */
export function capabilityScopeLabel(value: unknown): string {
  return normalizeCapabilityScope(value) === 'sop_specific' ? '仅限 SOP' : '通用';
}

/** 能力范围只读徽标：仅限 SOP 用橙色系配色，通用用蓝色系配色。 */
export function CapabilityScopeBadge({
  value,
  className,
}: {
  value: unknown;
  className?: string;
}) {
  const scope = normalizeCapabilityScope(value);
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-[10px] py-[4px] text-[10px] leading-none whitespace-nowrap',
        scope === 'sop_specific'
          ? 'bg-[#fff2e5] text-[#c65f00]'
          : 'bg-[#edf4ff] text-[#1a71ff]',
        className,
      )}
    >
      {capabilityScopeLabel(scope)}
    </span>
  );
}

/**
 * 能力范围切换控件：在「通用」与「仅限 SOP」之间切换，并展示对应场景说明。
 *
 * 开关回传 'sop_specific' 或 'general'；compact 为 true 时省略行内说明，仅保留标题旁的提示气泡。
 * resourceType 决定气泡与行内说明的措辞，disabled 为 true 时开关不可交互。
 */
export function CapabilityScopeControl({
  value,
  onChange,
  disabled = false,
  className,
  compact = false,
  resourceType,
}: {
  value: CapabilityScope;
  onChange: (value: CapabilityScope) => void;
  disabled?: boolean;
  className?: string;
  compact?: boolean;
  resourceType: CapabilityScopeResourceType;
}) {
  const isSopSpecific = normalizeCapabilityScope(value) === 'sop_specific';
  return (
    <div
      className={cn(
        'flex items-center justify-between gap-[16px] rounded-[12px] border border-[#eceef1] bg-[#fafbfc]',
        compact ? 'px-[12px] py-[10px]' : 'px-[14px] py-[12px]',
        className,
      )}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-[6px]">
          <span className="text-[13px] font-medium text-[#18181a]">能力范围</span>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                aria-label="查看仅限 SOP 说明"
                className="inline-flex size-[18px] shrink-0 items-center justify-center rounded-full text-[#8b93a7] outline-none transition-colors hover:text-[#18181a] focus-visible:ring-2 focus-visible:ring-[#1a71ff]/40"
              >
                <InfoCircleOutlined className="size-[14px]" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="top" align="start" className="max-w-[360px] leading-[1.55]">
              {SOP_SPECIFIC_SCOPE_DESCRIPTIONS[resourceType]}
            </TooltipContent>
          </Tooltip>
        </div>
        {!compact && (
          <p className="mt-[2px] text-[12px] leading-[1.55] text-[#858b9c]">
            {isSopSpecific ? SOP_SPECIFIC_INLINE_DESCRIPTIONS[resourceType] : '闲聊与 SOP 执行中均可自主调用。'}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-[8px]">
        <span className={cn('text-[12px]', !isSopSpecific ? 'font-medium text-[#18181a]' : 'text-[#858b9c]')}>
          通用
        </span>
        <Switch
          checked={isSopSpecific}
          disabled={disabled}
          aria-label="切换能力范围"
          onCheckedChange={(checked) => onChange(checked ? 'sop_specific' : 'general')}
        />
        <span className={cn('text-[12px]', isSopSpecific ? 'font-medium text-[#18181a]' : 'text-[#858b9c]')}>
          仅限 SOP
        </span>
      </div>
    </div>
  );
}
