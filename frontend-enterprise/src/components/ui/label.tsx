import * as React from "react"
import { Label as LabelPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

/**
 * 表单标签组件，基于 Radix Label.Root 封装统一样式。
 *
 * - 返回语义：渲染 Radix 标签根节点，自动关联同一表单控件的可访问性语义。
 * - 边界条件：传入的 className 与内置样式经 cn 合并，调用方样式优先级更高。
 */
function Label({
  className,
  ...props
}: React.ComponentProps<typeof LabelPrimitive.Root>) {
  return (
    <LabelPrimitive.Root
      data-slot="label"
      className={cn(
        "flex items-center gap-2 text-sm leading-none font-medium select-none group-data-[disabled=true]:pointer-events-none group-data-[disabled=true]:opacity-50 peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
}

export { Label }
