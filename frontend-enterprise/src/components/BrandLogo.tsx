import { cn } from '@/lib/utils';
import logoMark from '../assets/LOGO.svg';

export type BrandLogoProps = {
  /** 仅渲染 Logo 图形，隐藏 OpenBMB / StaffDeck 文字标识。 */
  markOnly?: boolean;
  /** Logo 图形的边长（像素）。 */
  markSize?: number;
  className?: string;
  /** 文字标识容器上的额外类名（例如用于按断点隐藏文字）。 */
  wordmarkClassName?: string;
};

/** 品牌 Logo 组合（图形标识 + OpenBMB / StaffDeck 文字标识）。Figma 节点 504:7137。 */
export default function BrandLogo({
  markOnly = false,
  markSize = 28,
  className,
  wordmarkClassName,
}: BrandLogoProps) {
  return (
    <span className={cn('flex items-center gap-[8px] overflow-hidden p-[4px]', className)}>
      <img
        src={logoMark}
        alt="StaffDeck"
        className="shrink-0"
        style={{ width: markSize, height: markSize }}
      />
      {!markOnly && (
        <span className={cn('flex flex-col items-center gap-[2px] leading-none', wordmarkClassName)}>
          {/* <span className="text-[12px] font-semibold leading-none text-[#0f136c]">
            OpenBMB
          </span> */}
          <strong className="text-[17px] font-semibold leading-none text-[#18181a]">
            StaffDeck
          </strong>
        </span>
      )}
    </span>
  );
}
