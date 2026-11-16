import { cva, type VariantProps } from "class-variance-authority";
import { AlertCircle, CheckCircle2, Info, TriangleAlert } from "lucide-react";
import { forwardRef, type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

const alertVariants = cva(
  "relative grid w-full grid-cols-[0_1fr] items-start gap-y-0.5 rounded-control border px-3.5 py-3 text-[13px] leading-relaxed has-[>svg]:grid-cols-[1rem_1fr] has-[>svg]:gap-x-2.5 [&>svg]:size-4 [&>svg]:translate-y-0.5",
  {
    variants: {
      variant: {
        default: "border-border bg-surface-2 text-body",
        destructive: "border-danger bg-danger-soft text-danger-text",
        warning: "border-warning bg-warning-soft text-warning-text",
        success: "border-success bg-success-soft text-success-text",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

const icons = {
  default: Info,
  destructive: AlertCircle,
  warning: TriangleAlert,
  success: CheckCircle2,
} as const;

export interface AlertProps
  extends HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof alertVariants> {}

export const Alert = forwardRef<HTMLDivElement, AlertProps>(
  ({ className, variant = "default", children, ...props }, ref) => {
    const Icon = icons[variant ?? "default"];
    return (
      <div
        ref={ref}
        role="alert"
        data-slot="alert"
        className={cn(alertVariants({ variant }), className)}
        {...props}
      >
        <Icon aria-hidden="true" className="shrink-0" strokeWidth={1.75} />
        {children}
      </div>
    );
  },
);
Alert.displayName = "Alert";

export const AlertTitle = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      data-slot="alert-title"
      className={cn("col-start-2 line-clamp-1 min-h-4 font-medium tracking-tight", className)}
      {...props}
    />
  ),
);
AlertTitle.displayName = "AlertTitle";

export const AlertDescription = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      data-slot="alert-description"
      className={cn("col-start-2 grid justify-items-start gap-1 text-[13px] opacity-90", className)}
      {...props}
    />
  ),
);
AlertDescription.displayName = "AlertDescription";
