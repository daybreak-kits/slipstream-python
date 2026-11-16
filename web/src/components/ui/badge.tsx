import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type HTMLAttributes, type Ref } from "react";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex w-fit shrink-0 items-center justify-center gap-1 overflow-hidden whitespace-nowrap rounded-badge border px-2 py-0.5 text-xs font-medium leading-none transition-colors focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background [&_svg]:pointer-events-none [&_svg]:h-3 [&_svg]:w-3 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary-soft text-primary",
        secondary: "border-border bg-surface-2 text-muted-foreground",
        success: "border-success bg-success-soft text-success-text",
        warning: "border-warning bg-warning-soft text-warning-text",
        danger: "border-danger bg-danger-soft text-danger-text",
        outline: "border-border bg-transparent text-body",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>["variant"]>;

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {
  /** Alias for variant, kept so callers can talk about semantic tones. */
  tone?: BadgeVariant;
  /** Render the child element instead of a native <span> (shadcn Slot). */
  asChild?: boolean;
}

export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, variant, tone, asChild = false, ...props }, ref) => {
    const resolvedVariant = tone ?? variant;
    const classes = cn(badgeVariants({ variant: resolvedVariant }), className);
    const data = {
      "data-slot": "badge",
      "data-variant": resolvedVariant ?? "default",
    } as const;

    if (asChild) {
      return (
        <Slot ref={ref as Ref<HTMLElement>} className={classes} {...data} {...props} />
      );
    }

    return <span ref={ref} className={classes} {...data} {...props} />;
  },
);
Badge.displayName = "Badge";

export { badgeVariants };
