import * as LabelPrimitive from "@radix-ui/react-label";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";

import { cn } from "@/lib/utils";

export interface LabelProps extends ComponentPropsWithoutRef<typeof LabelPrimitive.Root> {}

export const Label = forwardRef<ElementRef<typeof LabelPrimitive.Root>, LabelProps>(
  ({ className, ...props }, ref) => (
    <LabelPrimitive.Root
      ref={ref}
      data-slot="label"
      className={cn(
        "inline-flex items-center gap-2 text-[13px] font-medium leading-none text-title select-none",
        className,
      )}
      {...props}
    />
  ),
);
Label.displayName = "Label";
