/**
 * 企业端前端路由枚举：集中定义各页面路径常量，供路由注册与跳转复用。
 *
 * - 边界条件：路径均为绝对路径，需与路由表保持一致，避免出现未注册地址。
 */
export enum EnterpriseRoute {
  Workspace = '/workspace',
  Chat = '/workspace/chat',
  Platform = '/enterprise/platform',
  Gallery = '/workspace/gallery',
  Agents = '/enterprise/agents',
  Teams = '/enterprise/teams',
  Dashboard = '/enterprise/dashboard',
  ScheduledTasks = '/enterprise/scheduled-tasks',
  Memories = '/enterprise/memories',
  Feedback = '/enterprise/feedback',
  Channels = '/enterprise/channels',
  Knowledge = '/enterprise/knowledge',
  GeneralSkills = '/enterprise/general-skills',
  Skills = '/enterprise/skills',
  Tools = '/enterprise/tools',
  Accounts = '/enterprise/accounts',
  Models = '/enterprise/models',
  RuntimeSettings = '/enterprise/runtime-settings',
}
