import { cn } from "@/lib/utils"

/**
 * 骨架屏占位组件，用于内容加载期间的占位展示。
 *
 * 渲染带 `animate-pulse` 动画与 `bg-muted` 背景的 div，
 * 并透传 className 及其余 div 属性；
 * 具体尺寸与圆角由调用方通过 className 指定。
 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  )
}

export { Skeleton }
