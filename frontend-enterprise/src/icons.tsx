import type { CSSProperties } from 'react';
import StaffdeckIcon, { type StaffdeckIconName } from './components/StaffdeckIcon';

type IconProps = {
  className?: string;
  rotate?: number;
  spin?: boolean;
  style?: CSSProperties;
  [key: string]: unknown;
};

/**
 * 图标统一包装组件：把 antd 风格的图标属性转译到内置 StaffdeckIcon。
 *
 * @param rotate 旋转角度（度），未传或为 0 时不追加 transform。
 * @param spin 是否启用旋转动画样式类 `sd1-icon-spin`。
 * @returns 合并了外部 style.transform 与旋转角度的图标节点。
 */
function Sd1AntIcon({ name, rotate = 0, spin = false, className = '', style }: IconProps & { name: StaffdeckIconName }) {
  const transform = [style?.transform, rotate ? `rotate(${rotate}deg)` : ''].filter(Boolean).join(' ');
  return (
    <StaffdeckIcon
      name={name}
      className={`${spin ? 'sd1-icon-spin ' : ''}${className}`.trim()}
      style={{ ...style, transform: transform || undefined }}
    />
  );
}

/** 模型 / API 图标。 */
export const ApiOutlined = (props: IconProps) => <Sd1AntIcon name="model" {...props} />;
/** 应用网格图标。 */
export const AppstoreOutlined = (props: IconProps) => <Sd1AntIcon name="grid" {...props} />;
/** 左箭头图标（arrow 旋转 180°）。 */
export const ArrowLeftOutlined = (props: IconProps) => <Sd1AntIcon name="arrow" rotate={180} {...props} />;
/** 审计 / 文件图标。 */
export const AuditOutlined = (props: IconProps) => <Sd1AntIcon name="file" {...props} />;
/** 分支图标。 */
export const BranchesOutlined = (props: IconProps) => <Sd1AntIcon name="branch" {...props} />;
/** 实心风格对勾图标。 */
export const CheckCircleFilled = (props: IconProps) => <Sd1AntIcon name="check" {...props} />;
/** 空心风格对勾图标。 */
export const CheckCircleOutlined = (props: IconProps) => <Sd1AntIcon name="check" {...props} />;
/** 对勾图标，常用于菜单选中态。 */
export const CheckOutlined = (props: IconProps) => <Sd1AntIcon name="check" {...props} />;
/** 时钟图标。 */
export const ClockCircleOutlined = (props: IconProps) => <Sd1AntIcon name="clock" {...props} />;
/** 圆形关闭图标。 */
export const CloseCircleOutlined = (props: IconProps) => <Sd1AntIcon name="close" {...props} />;
/** 关闭图标。 */
export const CloseOutlined = (props: IconProps) => <Sd1AntIcon name="close" {...props} />;
/** 云图标。 */
export const CloudOutlined = (props: IconProps) => <Sd1AntIcon name="cloud" {...props} />;
/** 云同步图标（复用 refresh）。 */
export const CloudSyncOutlined = (props: IconProps) => <Sd1AntIcon name="refresh" {...props} />;
/** 代码图标。 */
export const CodeOutlined = (props: IconProps) => <Sd1AntIcon name="code" {...props} />;
/** 数据库图标。 */
export const DatabaseOutlined = (props: IconProps) => <Sd1AntIcon name="database" {...props} />;
/** 删除图标（垃圾桶）。 */
export const DeleteOutlined = (props: IconProps) => <Sd1AntIcon name="trash" {...props} />;
/** 桌面 / 设备图标。 */
export const DesktopOutlined = (props: IconProps) => <Sd1AntIcon name="desktop" {...props} />;
/** 下箭头图标（arrow 旋转 90°）。 */
export const DownOutlined = (props: IconProps) => <Sd1AntIcon name="arrow" rotate={90} {...props} />;
/** 下载图标。 */
export const DownloadOutlined = (props: IconProps) => <Sd1AntIcon name="download" {...props} />;
/** 编辑图标。 */
export const EditOutlined = (props: IconProps) => <Sd1AntIcon name="edit" {...props} />;
/** 实验 / 工具图标。 */
export const ExperimentOutlined = (props: IconProps) => <Sd1AntIcon name="tool" {...props} />;
/** 预览 / 可见图标。 */
export const EyeOutlined = (props: IconProps) => <Sd1AntIcon name="eye" {...props} />;
/** 新增文件图标（复用 plus）。 */
export const FileAddOutlined = (props: IconProps) => <Sd1AntIcon name="plus" {...props} />;
/** Markdown 文件图标。 */
export const FileMarkdownOutlined = (props: IconProps) => <Sd1AntIcon name="file" {...props} />;
/** 文件检索图标。 */
export const FileSearchOutlined = (props: IconProps) => <Sd1AntIcon name="file" {...props} />;
/** 文本文件图标。 */
export const FileTextOutlined = (props: IconProps) => <Sd1AntIcon name="file" {...props} />;
/** 打开的文件夹图标。 */
export const FolderOpenOutlined = (props: IconProps) => <Sd1AntIcon name="folder" {...props} />;
/** GitHub 图标（复用 code）。 */
export const GithubOutlined = (props: IconProps) => <Sd1AntIcon name="code" {...props} />;
/** 历史记录图标。 */
export const HistoryOutlined = (props: IconProps) => <Sd1AntIcon name="history" {...props} />;
/** 身份 / 证件图标（复用 user）。 */
export const IdcardOutlined = (props: IconProps) => <Sd1AntIcon name="user" {...props} />;
/** 收件箱图标。 */
export const InboxOutlined = (props: IconProps) => <Sd1AntIcon name="inbox" {...props} />;
/** 信息提示图标。 */
export const InfoCircleOutlined = (props: IconProps) => <Sd1AntIcon name="info" {...props} />;
/** 加载中图标（refresh 加旋转动画）。 */
export const LoadingOutlined = (props: IconProps) => <Sd1AntIcon name="refresh" spin {...props} />;
/** 锁图标。 */
export const LockOutlined = (props: IconProps) => <Sd1AntIcon name="lock" {...props} />;
/** 消息 / 会话图标。 */
export const MessageOutlined = (props: IconProps) => <Sd1AntIcon name="chat" {...props} />;
/** 深色模式（月亮）图标。 */
export const MoonOutlined = (props: IconProps) => <Sd1AntIcon name="moon" {...props} />;
/** 更多操作图标。 */
export const MoreOutlined = (props: IconProps) => <Sd1AntIcon name="more" {...props} />;
/** 暂停图标。 */
export const PauseCircleOutlined = (props: IconProps) => <Sd1AntIcon name="pause" {...props} />;
/** 播放图标。 */
export const PlayCircleOutlined = (props: IconProps) => <Sd1AntIcon name="play" {...props} />;
/** 加号图标。 */
export const PlusOutlined = (props: IconProps) => <Sd1AntIcon name="plus" {...props} />;
/** 筛选 / 概览图标（复用 filter）。 */
export const ProfileOutlined = (props: IconProps) => <Sd1AntIcon name="filter" {...props} />;
/** 刷新图标。 */
export const ReloadOutlined = (props: IconProps) => <Sd1AntIcon name="refresh" {...props} />;
/** 右箭头图标。 */
export const RightOutlined = (props: IconProps) => <Sd1AntIcon name="arrow" {...props} />;
/** 回滚图标（复用 history）。 */
export const RollbackOutlined = (props: IconProps) => <Sd1AntIcon name="history" {...props} />;
/** 保存图标。 */
export const SaveOutlined = (props: IconProps) => <Sd1AntIcon name="save" {...props} />;
/** 搜索图标。 */
export const SearchOutlined = (props: IconProps) => <Sd1AntIcon name="search" {...props} />;
/** 发送图标。 */
export const SendOutlined = (props: IconProps) => <Sd1AntIcon name="send" {...props} />;
/** 方案 / 灵感图标（复用 spark）。 */
export const SolutionOutlined = (props: IconProps) => <Sd1AntIcon name="spark" {...props} />;
/** 停止图标。 */
export const StopOutlined = (props: IconProps) => <Sd1AntIcon name="stop" {...props} />;
/** 浅色模式（太阳）图标。 */
export const SunOutlined = (props: IconProps) => <Sd1AntIcon name="sun" {...props} />;
/** 同步图标。 */
export const SyncOutlined = (props: IconProps) => <Sd1AntIcon name="refresh" {...props} />;
/** 团队图标（复用 user）。 */
export const TeamOutlined = (props: IconProps) => <Sd1AntIcon name="user" {...props} />;
/** 工具图标。 */
export const ToolOutlined = (props: IconProps) => <Sd1AntIcon name="tool" {...props} />;
/** 上传图标。 */
export const UploadOutlined = (props: IconProps) => <Sd1AntIcon name="upload" {...props} />;
/** 用户图标。 */
export const UserOutlined = (props: IconProps) => <Sd1AntIcon name="user" {...props} />;
/** 添加用户组图标（复用 user）。 */
export const UsergroupAddOutlined = (props: IconProps) => <Sd1AntIcon name="user" {...props} />;
/** 警告图标。 */
export const WarningOutlined = (props: IconProps) => <Sd1AntIcon name="warning" {...props} />;
