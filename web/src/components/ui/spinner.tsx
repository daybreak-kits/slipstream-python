import { cva, type VariantProps } from "class-variance-authority";
import { LoaderCircle } from "lucide-react";
import { forwardRef, type SVGProps } from "react";

import { cn } from "@/lib/utils";

const spinnerVariants = cva("animate-spin text-current motion-reduce:animate-none", {
  variants: {
    size: {
      sm: "h-3.5 w-3.5",
      md: "h-4 w-4",
      lg: "h-5 w-5",
    },
  },
  defaultVariants: {
    size: "md",
  },
});

export interface SpinnerProps
  extends Omit<SVGProps<SVGSVGElement>, "ref">,
    VariantProps<typeof spinnerVariants> {
  label?: string;
}

export const Spinner = forwardRef<SVGSVGElement, SpinnerProps>(
  ({ className, size, label = "加载中", ...props }, ref) => (
    <LoaderCircle
      ref={ref}
      role="status"
      aria-label={label}
      data-slot="spinner"
      strokeWidth={1.75}
      className={cn(spinnerVariants({ size }), className)}
      {...props}
    />
  ),
);
Spinner.displayName = "Spinner";

export { spinnerVariants };
