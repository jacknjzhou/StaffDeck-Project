import type { AgentProfileRead, AgentResourceBindingRead, AgentResourceType } from './types';
import {
  isEmployeeOwnedBy,
  isEnterpriseAdmin,
  isGalleryEmployee,
  type EnterpriseAuthUser,
} from './auth';

import avatarAfterSales from './assets/staffdeck/staffdeck-avatar-after-sales.png';
import avatarCommerce from './assets/staffdeck/staffdeck-avatar-commerce.png';
import avatarData from './assets/staffdeck/staffdeck-avatar-data.png';
import avatarDefault from './assets/staffdeck/staffdeck-avatar-default.png';
import avatarKnowledge from './assets/staffdeck/staffdeck-avatar-knowledge.png';
import avatarMarketing from './assets/staffdeck/staffdeck-avatar-marketing.png';
import avatarOps from './assets/staffdeck/staffdeck-avatar-ops.png';
import avatarOverall from './assets/staffdeck/staffdeck-avatar-overall.png';
import avatarProcurement from './assets/staffdeck/staffdeck-avatar-procurement.png';
import avatarProject from './assets/staffdeck/staffdeck-avatar-project.png';
import avatarQuality from './assets/staffdeck/staffdeck-avatar-quality.png';
import avatarSales from './assets/staffdeck/staffdeck-avatar-sales.png';
import avatarService from './assets/staffdeck/staffdeck-avatar-service.png';

export type EmployeeProfile = {
  roleKey: string;
  roleName: string;
  avatarText: string;
  avatarTone: string;
  avatarKind: 'preset' | 'upload';
  avatarPreset: string;
  avatarImage: string;
  onboardedAt: string;
  workStyles: string[];
  expertiseTags: string[];
  workModes: string[];
};

export type EmployeeAvatarPreset = {
  key: string;
  label: string;
  text: string;
  tone: string;
};

export type EmployeeTemplate = {
  key: string;
  roleName: string;
  avatarText: string;
  avatarTone: string;
  avatarPreset: string;
  description: string;
  workStyles: string[];
  expertiseTags: string[];
  workModes: string[];
};

type EmployeeAgentLike = {
  id?: string;
  name?: string;
  is_overall?: boolean;
  metadata?: Record<string, unknown>;
};

export const EMPLOYEE_AVATAR_PRESETS: EmployeeAvatarPreset[] = [
  { key: 'service-orbit', label: '研发员工', text: '研', tone: 'teal' },
  { key: 'after-sales-seal', label: '行政员工', text: '行', tone: 'copper' },
  { key: 'knowledge-node', label: '知识运营员工', text: '知', tone: 'olive' },
  { key: 'commerce-compass', label: '财务员工', text: '财', tone: 'blue' },
  { key: 'ops-grid', label: '人事员工', text: '人', tone: 'ink' },
  { key: 'quality-star', label: '法务员工', text: '法', tone: 'gold' },
  { key: 'sales-handshake', label: '销售员工', text: '销', tone: 'cobalt' },
  { key: 'marketing-spark', label: '市场员工', text: '市', tone: 'coral' },
  { key: 'procurement-check', label: '采购员工', text: '采', tone: 'forest' },
  { key: 'project-board', label: '项目管理员工', text: '项', tone: 'charcoal' },
  { key: 'data-insight', label: '数据分析员工', text: '数', tone: 'navy' },
];

export const DEFAULT_AVATAR_PRESET = 'service-orbit';

const PRESET_AVATAR_IMAGES: Record<string, string> = {
  'service-orbit': avatarService,
  'after-sales-seal': avatarAfterSales,
  'knowledge-node': avatarKnowledge,
  'commerce-compass': avatarCommerce,
  'ops-grid': avatarOps,
  'quality-star': avatarQuality,
  'sales-handshake': avatarSales,
  'marketing-spark': avatarMarketing,
  'procurement-check': avatarProcurement,
  'project-board': avatarProject,
  'data-insight': avatarData,
  overall: avatarOverall,
};

type AvatarSource = Pick<EmployeeProfile, 'avatarKind' | 'avatarImage' | 'avatarPreset'>;

/** 判断员工头像是否为已上传的自定义图片（需同时满足上传类型与图片地址非空）。 */
export function isUploadedAvatar(profile: AvatarSource): boolean {
  return profile.avatarKind === 'upload' && Boolean(profile.avatarImage);
}

/** 解析员工头像图片地址：上传图片优先，否则按预设头像键回退到内置插画或默认头像。 */
export function employeeAvatarImage(profile: AvatarSource): string {
  if (isUploadedAvatar(profile)) return profile.avatarImage;
  return PRESET_AVATAR_IMAGES[profile.avatarPreset || DEFAULT_AVATAR_PRESET] || avatarDefault;
}

export const EMPLOYEE_TEMPLATES: EmployeeTemplate[] = [
  {
    key: 'service-specialist',
    roleName: '研发',
    avatarText: '研',
    avatarTone: 'teal',
    avatarPreset: 'service-orbit',
    description: '负责研发资料查询、代码任务拆解、SOP 执行和交付记录沉淀。',
    workStyles: ['目标明确', '证据优先', '动作可追溯'],
    expertiseTags: ['研发协作', '代码检索', 'SOP 执行'],
    workModes: ['理解需求', '检索资料', '推进执行'],
  },
  {
    key: 'after-sales',
    roleName: '行政',
    avatarText: '行',
    avatarTone: 'copper',
    avatarPreset: 'after-sales-seal',
    description: '负责会议纪要、资料归档、跨部门事务跟进和结果同步。',
    workStyles: ['流程推进', '及时追问', '留痕复盘'],
    expertiseTags: ['资料归档', '会议纪要', '事务跟进'],
    workModes: ['确认事项', '拆解步骤', '同步结果'],
  },
  {
    key: 'knowledge-operator',
    roleName: '知识运营',
    avatarText: '知',
    avatarTone: 'olive',
    avatarPreset: 'knowledge-node',
    description: '负责知识库检索、资料结构化归档、信息核对和答案沉淀。',
    workStyles: ['证据优先', '结构清晰', '持续沉淀'],
    expertiseTags: ['知识检索', '资料归档', '信息结构化'],
    workModes: ['查资料', '做归档', '给答案'],
  },
  {
    key: 'commerce-guide',
    roleName: '财务',
    avatarText: '财',
    avatarTone: 'blue',
    avatarPreset: 'commerce-compass',
    description: '负责报销核对、预算口径、财务资料检索和风险提示。',
    workStyles: ['证据优先', '口径统一', '风险克制'],
    expertiseTags: ['报销核对', '预算口径', '数据复盘'],
    workModes: ['查规则', '核凭证', '给结论'],
  },
  {
    key: 'sales-advisor',
    roleName: '客户拓展顾问',
    avatarText: '销',
    avatarTone: 'cobalt',
    avatarPreset: 'sales-handshake',
    description: '围绕客户需求澄清、商机推进和沟通准备提供结构化建议，帮助销售团队形成下一步行动，并在信息不足或需要承诺时明确提示人工确认。',
    workStyles: ['目标清晰', '审慎承诺'],
    expertiseTags: ['需求澄清', '商机推进', '客户沟通'],
    workModes: ['问答', '流程引导'],
  },
  {
    key: 'marketing-planner',
    roleName: '市场内容策划',
    avatarText: '市',
    avatarTone: 'coral',
    avatarPreset: 'marketing-spark',
    description: '协助梳理市场目标、受众和渠道，形成内容策划简报与发布检查清单，避免虚构数据、未授权素材和未经确认的对外承诺。',
    workStyles: ['受众导向', '事实审慎'],
    expertiseTags: ['内容策划', '活动简报', '发布检查'],
    workModes: ['问答', '内容规划'],
  },
  {
    key: 'procurement-coordinator',
    roleName: '采购协同专员',
    avatarText: '采',
    avatarTone: 'forest',
    avatarPreset: 'procurement-check',
    description: '帮助员工准备采购需求、供应商比较维度和审批材料，强调需求可比、职责分离与留痕，不替代有权限的采购或审批人员作出承诺。',
    workStyles: ['合规优先', '信息可追溯'],
    expertiseTags: ['采购需求', '供应商比较', '审批准备'],
    workModes: ['问答', '流程引导'],
  },
  {
    key: 'project-manager',
    roleName: '项目推进经理',
    avatarText: '项',
    avatarTone: 'charcoal',
    avatarPreset: 'project-board',
    description: '协助团队拆解目标、里程碑、责任人和风险，输出可执行的推进清单，遇到跨团队冲突或范围变化时提示升级决策。',
    workStyles: ['行动导向', '风险透明'],
    expertiseTags: ['里程碑', '风险跟踪', '协作推进'],
    workModes: ['问答', '项目梳理'],
  },
  {
    key: 'data-analyst',
    roleName: '经营分析师',
    avatarText: '数',
    avatarTone: 'navy',
    avatarPreset: 'data-insight',
    description: '帮助业务人员定义指标口径、分析周期和对比维度，给出可复核的分析框架，明确区分已知事实、计算结果与推测。',
    workStyles: ['口径严谨', '结论可追溯'],
    expertiseTags: ['指标口径', '趋势分析', '经营复盘'],
    workModes: ['问答', '分析框架'],
  },
];

/** 原样返回展示文案，作为员工相关文案的统一取值入口（当前不做任何转换）。 */
export function staffdeckDisplayText(value: string): string {
  return value;
}

/** 判断代理是否被标记为默认数字员工（广场代理始终返回 false）。 */
export function isDefaultEmployeeAgent(agent?: EmployeeAgentLike | null): boolean {
  if (!agent || agent.is_overall) return false;
  const metadata = agent.metadata || {};
  return metadata.is_default_employee === true;
}

/** 优先选取默认数字员工，否则回退到第一个非广场代理；没有可用项时返回 undefined。 */
export function preferredEmployeeAgent<T extends EmployeeAgentLike>(agents: T[]): T | undefined {
  return agents.find(isDefaultEmployeeAgent) || agents.find((item) => !item.is_overall);
}

export type EmployeeVisibilityOptions = {
  activeOnly?: boolean;
  excludeAgentId?: string;
  includeDefault?: boolean;
  includeOverall?: boolean;
};

/**
 * 判断当前用户是否可在员工列表中看到指定代理。
 *
 * - 先按 excludeAgentId、activeOnly 选项做排除。
 * - 管理员可额外看到广场代理；普通用户仅可见本人创建、广场公开或默认员工。
 */
export function canAccessEmployeeAgent(
  agent: AgentProfileRead,
  user?: EnterpriseAuthUser | null,
  options: EmployeeVisibilityOptions = {},
): boolean {
  if (options.excludeAgentId && agent.id === options.excludeAgentId) return false;
  if (options.activeOnly && agent.status !== 'active') return false;

  const includeOverall = options.includeOverall ?? false;
  if (isEnterpriseAdmin(user)) return includeOverall || !agent.is_overall;
  if (agent.is_overall) return false;

  const includeDefault = options.includeDefault ?? false;
  return (
    (includeDefault && isDefaultEmployeeAgent(agent))
    || isEmployeeOwnedBy(agent, user)
    || isGalleryEmployee(agent)
  );
}

/** 判断该代理是否被当前用户使用过（会话或聊天元数据任一命中即为真）。 */
export function isEmployeeUsedByCurrentUser(agent: AgentProfileRead): boolean {
  const metadata = agent.metadata || {};
  return metadata.used_by_current_user === true || metadata.chat_used_by_current_user === true;
}

/**
 * 判断当前用户能否在会话中选用指定代理。
 *
 * - 比可见性更严格：广场代理要求当前用户已使用过。
 * - 管理员对非广场代理默认可选，普通用户仍受创建者与使用记录限制。
 */
export function canSelectCurrentEmployeeAgent(
  agent: AgentProfileRead,
  user?: EnterpriseAuthUser | null,
  options: EmployeeVisibilityOptions = {},
): boolean {
  if (options.excludeAgentId && agent.id === options.excludeAgentId) return false;
  if (options.activeOnly && agent.status !== 'active') return false;

  const includeOverall = options.includeOverall ?? false;
  if (isEnterpriseAdmin(user)) {
    if (agent.is_overall) return includeOverall;
    if (isGalleryEmployee(agent) && !isEmployeeOwnedBy(agent, user)) {
      return isEmployeeUsedByCurrentUser(agent);
    }
    return true;
  }
  if (agent.is_overall) return false;

  const includeDefault = options.includeDefault ?? false;
  return (
    (includeDefault && isDefaultEmployeeAgent(agent))
    || isEmployeeOwnedBy(agent, user)
    || (isGalleryEmployee(agent) && isEmployeeUsedByCurrentUser(agent))
  );
}

/** 判断当前用户能否管理该代理：广场代理仅管理员可管理，其余为管理员或创建者。 */
export function canManageEmployeeAgent(
  agent: AgentProfileRead,
  user?: EnterpriseAuthUser | null,
): boolean {
  if (agent.is_overall) return isEnterpriseAdmin(user);
  return isEnterpriseAdmin(user) || isEmployeeOwnedBy(agent, user);
}

/** 判断该代理是否为当前用户自己创建的数字员工（广场代理始终返回 false）。 */
export function isMyEmployeeAgent(
  agent: AgentProfileRead,
  user?: EnterpriseAuthUser | null,
): boolean {
  return !agent.is_overall && isEmployeeOwnedBy(agent, user);
}

/** 按可见性规则过滤代理列表，返回当前用户可查看的员工。 */
export function visibleEmployeeAgents(
  rows: AgentProfileRead[],
  user?: EnterpriseAuthUser | null,
  options: EmployeeVisibilityOptions = {},
): AgentProfileRead[] {
  return rows.filter((agent) => canAccessEmployeeAgent(agent, user, options));
}

/** 按可选用规则过滤代理列表，返回当前用户可在会话中选用的员工。 */
export function currentEmployeeAgents(
  rows: AgentProfileRead[],
  user?: EnterpriseAuthUser | null,
  options: EmployeeVisibilityOptions = {},
): AgentProfileRead[] {
  return rows.filter((agent) => canSelectCurrentEmployeeAgent(agent, user, options));
}

/** 从代理列表中找出开放广场代理（is_overall），不存在时返回 undefined。 */
export function openGalleryAgent(rows: AgentProfileRead[]): AgentProfileRead | undefined {
  return rows.find((agent) => agent.is_overall);
}

/** 返回开放广场代理的 ID，列表中不存在时返回空字符串。 */
export function openGalleryAgentId(rows: AgentProfileRead[]): string {
  return openGalleryAgent(rows)?.id || '';
}

/** 构造导入来源下拉选项：存在广场代理时返回单个选项，否则返回空数组。 */
export function openGalleryImportSourceOptions(
  rows: AgentProfileRead[],
  label: string,
): Array<{ value: string; label: string }> {
  const agentId = openGalleryAgentId(rows);
  return agentId ? [{ value: agentId, label }] : [];
}

/** 将未知值归一化为非空字符串数组，非数组输入返回空数组。 */
function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}

/** 从元数据对象中读取字符串字段，字段缺失或类型不符时返回空字符串。 */
function stringFromMeta(metadata: Record<string, unknown>, key: string): string {
  const value = metadata[key];
  return typeof value === 'string' ? value : '';
}

/** 依次返回首个非空字符串（已去除首尾空白），全部为空时返回空字符串。 */
function firstString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

/** 从元数据的多个候选键解析创建者名称，均缺失时回退到 fallback。 */
export function creatorNameFromMetadata(
  metadata?: Record<string, unknown> | null,
  fallback = '',
): string {
  const meta = metadata || {};
  const creator = firstString(
    meta.creator_name,
    meta.created_by,
    meta.created_by_display_name,
    meta.created_by_username,
    meta.owner_display_name,
    meta.owner_username,
    meta.gallery_published_by,
    meta.created_by_user_id,
    meta.owner_user_id,
  );
  if (!creator) return fallback;
  const normalized = creator.trim();
  return normalized || fallback;
}

/** 拼接「名称 @创建者」，名称为空时显示「未命名」，无创建者时只返回名称。 */
export function displayNameWithCreator(name: string, creator?: string): string {
  const cleanName = name.trim() || '未命名';
  const cleanCreator = (creator || '').trim();
  if (!cleanCreator) return cleanName;
  return `${cleanName} @${cleanCreator}`;
}

/**
 * 由代理元数据推导完整的员工展示档案（岗位、头像、入职时间与标签）。
 *
 * - 广场代理固定使用「开放广场」身份与 overall 头像。
 * - 元数据缺失时依次回退到角色模板与默认头像预设。
 */
export function employeeProfile(agent?: AgentProfileRead | null): EmployeeProfile {
  const metadata = agent?.metadata || {};
  const template = EMPLOYEE_TEMPLATES.find((item) => item.key === metadata.role_key);
  const preset = EMPLOYEE_AVATAR_PRESETS.find((item) => item.key === metadata.avatar_preset)
    || (template ? EMPLOYEE_AVATAR_PRESETS.find((item) => item.key === template.avatarPreset) : undefined)
    || EMPLOYEE_AVATAR_PRESETS[0];
  const isOverall = Boolean(agent?.is_overall);
  const avatarKind = stringFromMeta(metadata, 'avatar_kind') === 'upload' && stringFromMeta(metadata, 'avatar_image')
    ? 'upload'
    : 'preset';
  return {
    roleKey: stringFromMeta(metadata, 'role_key') || template?.key || '',
    roleName: isOverall ? '开放广场' : stringFromMeta(metadata, 'role_name') || template?.roleName || '待补充岗位',
    avatarText: isOverall ? '广' : stringFromMeta(metadata, 'avatar_text') || preset.text || template?.avatarText || '员',
    avatarTone: isOverall ? 'overall' : stringFromMeta(metadata, 'avatar_tone') || preset.tone || template?.avatarTone || 'teal',
    avatarKind: isOverall ? 'preset' : avatarKind,
    avatarPreset: isOverall ? 'overall' : stringFromMeta(metadata, 'avatar_preset') || preset.key,
    avatarImage: isOverall ? '' : stringFromMeta(metadata, 'avatar_image'),
    onboardedAt: stringFromMeta(metadata, 'onboarded_at') || agent?.created_at?.slice(0, 10) || '-',
    workStyles: asStringArray(metadata.work_styles),
    expertiseTags: asStringArray(metadata.expertise_tags),
    workModes: asStringArray(metadata.work_modes),
  };
}

/** 返回员工展示名：空代理为「数字员工」，广场代理为「开放广场」，其余取代理名称。 */
export function employeeDisplayName(agent?: AgentProfileRead | null): string {
  if (!agent) return '数字员工';
  if (agent.is_overall) return '开放广场';
  return agent.name || '数字员工';
}

/** 返回员工创建者名称，缺少创建者信息时为空字符串。 */
export function employeeCreatorName(agent?: AgentProfileRead | null): string {
  return creatorNameFromMetadata(agent?.metadata);
}

/** 返回带创建者后缀的员工展示名，用于列表与详情中的归属标注。 */
export function employeeDisplayNameWithCreator(agent?: AgentProfileRead | null): string {
  return displayNameWithCreator(employeeDisplayName(agent), employeeCreatorName(agent));
}

/** 从资源元数据中解析创建者名称，用于资源归属展示。 */
export function resourceCreatorName(resource?: { metadata?: Record<string, unknown> } | null): string {
  return creatorNameFromMetadata(resource?.metadata);
}

/** 返回带创建者后缀的资源展示名。 */
export function resourceDisplayNameWithCreator(
  name: string,
  resource?: { metadata?: Record<string, unknown> } | null,
): string {
  return displayNameWithCreator(name, resourceCreatorName(resource));
}

/** 统计指定类型的有效资源绑定数量（排除 deleted 与 inactive 状态）。 */
export function resourceCount(resources: AgentResourceBindingRead[] | undefined, type: AgentResourceBindingRead['resource_type']): number {
  return (resources || []).filter((item) => (
    item.resource_type === type
    && item.status !== 'deleted'
    && item.status !== 'inactive'
  )).length;
}

/** 会话侧边栏可选的员工：当前用户可见且处于启用状态的数字员工。 */
export function visibleChatEmployees(
  rows: AgentProfileRead[],
  user?: EnterpriseAuthUser | null,
): AgentProfileRead[] {
  return currentEmployeeAgents(rows, user, { activeOnly: true });
}

/** 统计代理自身绑定中指定类型的有效资源数量（排除 deleted 与 inactive 状态）。 */
export function agentResourceCount(agent: AgentProfileRead, resourceType: AgentResourceType): number {
  return (agent.resources || []).filter((resource) => (
    resource.resource_type === resourceType
    && resource.status !== 'deleted'
    && resource.status !== 'inactive'
  )).length;
}

/** 统计状态为 active 的资源绑定数量。 */
export function activeResourceCount(resources: AgentResourceBindingRead[] | undefined): number {
  return (resources || []).filter((item) => item.status === 'active').length;
}

/** 基于员工模板生成写入代理的元数据，并保留 currentMetadata 中的既有字段。 */
export function employeeMetadataFromTemplate(templateKey: string, currentMetadata: Record<string, unknown> = {}): Record<string, unknown> {
  const template = EMPLOYEE_TEMPLATES.find((item) => item.key === templateKey) || EMPLOYEE_TEMPLATES[0];
  return {
    ...currentMetadata,
    role_key: template.key,
    role_name: template.roleName,
    avatar_text: template.avatarText,
    avatar_tone: template.avatarTone,
    avatar_kind: 'preset',
    avatar_preset: template.avatarPreset,
    onboarded_at: currentMetadata.onboarded_at || new Date().toISOString().slice(0, 10),
    work_styles: template.workStyles,
    expertise_tags: template.expertiseTags,
    work_modes: template.workModes,
  };
}

/**
 * 生成「空白入职」场景的员工元数据，缺失字段以默认岗位与默认头像补齐。
 *
 * - 保留 currentMetadata 中的既有字段，并标记 blank_onboarding。
 * - 入职时间缺失时取当天日期。
 */
export function employeeBlankMetadata(currentMetadata: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ...currentMetadata,
    blank_onboarding: true,
    role_key: stringFromMeta(currentMetadata, 'role_key'),
    role_name: stringFromMeta(currentMetadata, 'role_name') || '待补充职位',
    avatar_text: stringFromMeta(currentMetadata, 'avatar_text') || '员',
    avatar_tone: stringFromMeta(currentMetadata, 'avatar_tone') || 'teal',
    avatar_kind: stringFromMeta(currentMetadata, 'avatar_kind') || 'preset',
    avatar_preset: stringFromMeta(currentMetadata, 'avatar_preset') || EMPLOYEE_AVATAR_PRESETS[0].key,
    onboarded_at: currentMetadata.onboarded_at || new Date().toISOString().slice(0, 10),
    work_styles: asStringArray(currentMetadata.work_styles),
    expertise_tags: asStringArray(currentMetadata.expertise_tags),
    work_modes: asStringArray(currentMetadata.work_modes),
  };
}
