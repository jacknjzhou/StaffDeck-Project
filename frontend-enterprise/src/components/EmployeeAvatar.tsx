import type { CSSProperties } from 'react';

import {
  DEFAULT_AVATAR_PRESET,
  employeeAvatarImage,
  employeeProfile,
  type EmployeeProfile,
} from '../employee';
import type { AgentProfileRead } from '../types';

type AvatarProfile = Pick<EmployeeProfile, 'avatarKind' | 'avatarImage' | 'avatarPreset' | 'avatarText' | 'avatarTone'>;

type EmployeeAvatarProps = {
  agent?: AgentProfileRead | null;
  /** 已解析好的头像档案；省略时由 `agent` 推导。 */
  profile?: AvatarProfile;
  /** 宽高简写（px）；未单独传 width/height 时同时作用于两者。 */
  size?: number;
  /** 显式宽度（px），缺省回退到 `size`。 */
  width?: number;
  /** 显式高度（px），缺省回退到 `size`。 */
  height?: number;
  /** 圆角覆盖值（px 或任意 CSS 长度）。 */
  radius?: number | string;
  /** 图片填充方式，`cover` 可在不拉伸的前提下填满画框。 */
  fit?: CSSProperties['objectFit'];
  /** 图片在画框内的对齐位置，例如 `center bottom`。 */
  objectPosition?: CSSProperties['objectPosition'];
  className?: string;
  style?: CSSProperties;
};

/**
 * 员工头像组件：按头像档案渲染方形图片，可覆盖尺寸、圆角与裁切方式。
 *
 * - 传入 `profile` 时直接使用，否则由 `agent` 推导头像档案。
 * - `width` / `height` 缺省回退到 `size`，`radius` 未传则沿用样式表圆角。
 * - 图片统一铺满盒子并重置 transform，避免上下文样式导致拉伸或偏移。
 */
export default function EmployeeAvatar({
  agent,
  profile: profileOverride,
  size = 54,
  width,
  height,
  radius,
  fit = 'cover',
  objectPosition = 'center',
  className = '',
  style,
}: EmployeeAvatarProps) {
  const profile = profileOverride || employeeProfile(agent);

  const className_ = [
    'employee-avatar',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  const boxStyle: CSSProperties = {
    width: width ?? size,
    height: height ?? size,
    ...(radius != null ? { borderRadius: radius } : null),
    ...style,
  };

  // Lock the image to the box at any width/height: `cover` fills without distortion,
  // while resetting transform/max-width neutralizes per-context overrides.
  const imageStyle: CSSProperties = {
    width: '100%',
    height: '100%',
    maxWidth: 'none',
    objectFit: fit,
    objectPosition,
    transform: 'none'
  };

  return (
    <span
      className={className_}
      style={boxStyle}
      aria-label={`${profile.avatarText || '员'}员工头像`}
    >
      <img src={employeeAvatarImage(profile)} alt="" style={imageStyle} />
    </span>
  );
}
