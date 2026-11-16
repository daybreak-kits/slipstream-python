import { cva, type VariantProps } from "class-variance-authority";
import type { LucideIcon } from "lucide-react";
import { forwardRef, type HTMLAttributes, type ReactNode } from "react";

import { cn } from "@/lib/utils";

const emptyMediaVariants = cva(
  "mb-2 flex shrink-0 items-center justify-center [&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-transparent",
        icon: "flex size-12 shrink-0 items-center justify-center rounded-full bg-primary-soft text-primary [&_svg:not([class*='size-'])]:size-5",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

export const Empty = forwardRef<HTMLDivElement, EmptyProps>(
  ({ icon: Icon, title, description, action, className, children, ...props }, ref) => (
    <div
      ref={ref}
      data-slot="empty"
      className={cn(
        "flex min-w-0 flex-1 flex-col items-center justify-center gap-4 px-6 py-12 text-center text-balance",
        className,
      )}
      {...props}
    >
      {Icon ? (
        <EmptyMedia variant="icon">
          <Icon className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
        </EmptyMedia>
      ) : null}
      <EmptyHeader>
        <EmptyTitle>{title}</EmptyTitle>
        {description ? <EmptyDescription>{description}</EmptyDescription> : null}
      </EmptyHeader>
      {action ? <EmptyContent>{action}</EmptyContent> : null}
      {children}
    </div>
  ),
);
Empty.displayName = "Empty";

export const EmptyHeader = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      data-slot="empty-header"
      className={cn("flex max-w-md flex-col items-center gap-1.5 text-center", className)}
      {...props}
    />
  ),
);
EmptyHeader.displayName = "EmptyHeader";

export interface EmptyMediaProps
  extends HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof emptyMediaVariants> {}

export const EmptyMedia = forwardRef<HTMLDivElement, EmptyMediaProps>(
  ({ className, variant = "default", ...props }, ref) => (
    <div
      ref={ref}
      data-slot="empty-icon"
      data-variant={variant}
      className={cn(emptyMediaVariants({ variant }), className)}
      {...props}
    />
  ),
);
EmptyMedia.displayName = "EmptyMedia";

export const EmptyTitle = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      data-slot="empty-title"
      className={cn("text-base font-semibold tracking-tight text-title", className)}
      {...props}
    />
  ),
);
EmptyTitle.displayName = "EmptyTitle";

export const EmptyDescription = forwardRef<HTMLParagraphElement, HTMLAttributes<HTMLParagraphElement>>(
  ({ className, ...props }, ref) => (
    <p
      ref={ref}
      data-slot="empty-description"
      className={cn("max-w-md text-sm leading-relaxed text-muted-foreground", className)}
      {...props}
    />
  ),
);
EmptyDescription.displayName = "EmptyDescription";

export const EmptyContent = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      data-slot="empty-content"
      className={cn("flex w-full max-w-sm min-w-0 flex-col items-center gap-4 text-sm", className)}
      {...props}
    />
  ),
);
EmptyContent.displayName = "EmptyContent";

export interface EmptyProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  icon?: LucideIcon;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
}
