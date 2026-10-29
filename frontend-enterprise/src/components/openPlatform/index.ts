/**
 * 开放平台相关组件的统一出口（barrel 文件）。
 *
 * 集中再导出 PlatformColumn、PlatformEmployeeCard、PlatformEmployeeDrawer、
 * PlatformKindDetailView、PlatformResourceCard 与 PlatformResourceDrawer 及其类型，
 * 便于外部通过 `@/components/openPlatform` 一次性引入。
 */
export { default as PlatformColumn } from './PlatformColumn';
export type { PlatformColumnProps } from './PlatformColumn';
export { default as PlatformEmployeeCard } from './PlatformEmployeeCard';
export type { PlatformEmployeeCardProps, PlatformStat } from './PlatformEmployeeCard';
export { default as PlatformEmployeeDrawer } from './PlatformEmployeeDrawer';
export type { PlatformEmployeeDrawerProps } from './PlatformEmployeeDrawer';
export { default as PlatformKindDetailView } from './PlatformKindDetailView';
export type { PlatformDetailItem, PlatformDetailKind, PlatformKindDetailViewProps } from './PlatformKindDetailView';
export { default as PlatformResourceCard } from './PlatformResourceCard';
export type { PlatformResourceCardProps, PlatformResourceAccent } from './PlatformResourceCard';
export { platformResourceAccentStyles } from './PlatformResourceCard';
export { default as PlatformResourceDrawer } from './PlatformResourceDrawer';
export type { PlatformResourceDrawerProps } from './PlatformResourceDrawer';
