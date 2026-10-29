import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

/** 合并并消解冲突的 Tailwind 类名，返回可直接用于 className 的字符串。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
