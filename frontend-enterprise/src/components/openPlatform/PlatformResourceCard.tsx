import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

import IconFolder from '../../assets/icons/cap-folder.svg?react';

/** 各模块的强调色，用于信息行与标签胶囊（SD1 232:4634 系列）。 */
export type PlatformResourceAccent = 'green' | 'blue' | 'indigo' | 'orange';

const ACCENT_STYLES: Record<PlatformResourceAccent, { meta: string; tag: string }> = {
  green: { meta: 'text-[#2cb360]', tag: 'bg-[#e9f7ef] text-[#2cb360]' },
  blue: { meta: 'text-[#27c9ff]', tag: 'bg-[#c4f1ff] text-[#25c7ff]' },
  indigo: { meta: 'text-[#1a71ff]', tag: 'bg-[#e8f0ff] text-[#1a71ff]' },
  orange: { meta: 'text-[#ff7f00]', tag: 'bg-[#fff2e5] text-[#ff7f00]' },
};

export const platformResourceAccentStyles = ACCENT_STYLES;

export type PlatformResourceCardProps = {
  title: ReactNode;
  /** 标题下方的强调色信息行，例如 "12M / 6个片段"。 */
  meta: ReactNode;
  description: ReactNode;
  tags?: string[];
  /** 完整的 36px 图标视觉；省略时展示默认的文件夹图标块。 */
  icon?: ReactNode;
  /** 信息行与标签胶囊使用的模块强调色，默认为绿色（知识库）。 */
  accent?: PlatformResourceAccent;
  onClick?: () => void;
  className?: string;
};

/**
 * 广场资源卡片，供知识库 / 技能 / SOP / 工具模块共用。
 *
 * - 展示彩色模块图标、标题、强调色信息行、两行描述与一行标签胶囊（SD1 232:4923）。
 * - `icon` 缺省时回退为文件夹图标块，`accent` 决定信息行与胶囊的配色（默认绿色）。
 * - 整张卡片为 `button`，点击时调用 `onClick`，因此需由使用方保证可访问名称。
 */
export default function PlatformResourceCard({
  title,
  meta,
  description,
  tags,
  icon,
  accent = 'green',
  onClick,
  className,
}: PlatformResourceCardProps) {
  const accentStyles = ACCENT_STYLES[accent];
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'relative flex h-[112px] w-full shrink-0 flex-col items-center justify-center overflow-hidden rounded-[14px] border-[0.5px] border-[#f6f6f6] bg-white p-[4px] text-left backdrop-blur-[1.835px] transition-shadow hover:shadow-[0_8px_20px_rgba(15,23,42,0.06)]',
        '',
        className,
      )}
    >
      <div className="flex w-full flex-col items-start gap-[6px] px-[8px]">
        <div className="flex w-full items-center gap-[4px]">
          {icon ?? (
            <span className="grid size-[32px] shrink-0 place-items-center rounded-[10px] bg-[#f2f4f8] text-[#8a94a6]">
              <IconFolder className="size-[18px]" />
            </span>
          )}
          <div className="flex min-w-0 flex-1 flex-col gap-[4px]">
            <p className="truncate text-[12px] font-medium text-[#464c5e]">{title}</p>
            <p className={cn('truncate text-[10px]', accentStyles.meta)}>{meta}</p>
          </div>
        </div>

        <p className="line-clamp-2 h-[26px] w-full text-[10px] leading-[13px] text-[#757f9c]">
          {description}
        </p>

        {tags && tags.length > 0 && (
          <div className="flex flex-wrap items-center gap-[6px]">
            {tags.map((tag) => (
              <span
                key={tag}
                className={cn(
                  'inline-flex items-center rounded-[90px] px-[8px] py-[2px] text-[8px] leading-[normal]',
                  accentStyles.tag,
                )}
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>
    </button>
  );
}
