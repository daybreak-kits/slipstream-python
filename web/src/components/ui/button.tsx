import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes, type Ref } from "react";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-control text-sm font-medium transition-colors duration-150 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-on shadow-xs hover:bg-primary-hover active:bg-primary-active",
        secondary:
          "bg-surface-2 text-title hover:bg-[var(--border)] active:bg-[var(--border-strong)]",
        outline:
          "border border-border-strong bg-surface text-title hover:bg-surface-2 active:bg-[var(--surface-2)]",
        ghost: "text-body hover:bg-surface-2 hover:text-title active:bg-[var(--border)]",
        danger:
          "bg-danger text-primary-on shadow-xs hover:bg-danger hover:brightness-95 active:bg-danger active:brightness-90",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-10 px-4",
        md: "h-9 rounded-control px-3.5 text-[13px]",
        sm: "h-8 rounded-control px-3 text-[13px]",
        lg: "h-11 px-6",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  /** Render the child element instead of a native <button> (shadcn Slot). */
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type = "button", ...props }, ref) => {
    const classes = cn(buttonVariants({ variant, size }), className);
    const data = {
      "data-slot": "button",
      "data-variant": variant ?? "default",
      "data-size": size ?? "default",
    } as const;

    if (asChild) {
      return (
        <Slot ref={ref as Ref<HTMLElement>} className={classes} {...data} {...props} />
      );
    }

    return (
      <button ref={ref} type={type} className={classes} {...data} {...props} />
    );
  },
);
Button.displayName = "Button";

export { buttonVariants };
