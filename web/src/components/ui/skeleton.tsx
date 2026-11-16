import { forwardRef, type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export interface SkeletonProps extends HTMLAttributes<HTMLDivElement> {}

export const Skeleton = forwardRef<HTMLDivElement, SkeletonProps>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      data-slot="skeleton"
      aria-hidden="true"
      className={cn("animate-pulse rounded-control bg-surface-2 motion-reduce:animate-none", className)}
      {...props}
    />
  ),
);
Skeleton.displayName = "Skeleton";
