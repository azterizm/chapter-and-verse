import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

export const chipVariants = cva(
  "chip-press inline-flex items-center justify-center gap-1.5 rounded-full text-ui tabular-nums select-none cursor-pointer focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-40",
  {
    variants: {
      variant: {
        citation: "h-6 px-2",
        outline: "min-h-11 px-3.5 border border-border bg-surface text-foreground hover:bg-primary-soft hover:text-primary",
      },
      selected: {
        false: "",
        true: "",
      },
    },
    compoundVariants: [
      {
        variant: "citation",
        selected: false,
        className: "bg-primary-soft text-primary hover:bg-primary/15",
      },
      {
        variant: "citation",
        selected: true,
        className: "bg-primary text-primary-foreground",
      },
      {
        variant: "outline",
        selected: true,
        className: "border-primary bg-primary text-primary-foreground hover:bg-primary hover:text-primary-foreground",
      },
    ],
    defaultVariants: {
      variant: "citation",
      selected: false,
    },
  },
);

export interface ChipProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    Omit<VariantProps<typeof chipVariants>, "selected"> {
  selected?: boolean | undefined;
  asChild?: boolean;
}

export const Chip = React.forwardRef<HTMLButtonElement, ChipProps>(function Chip(
  { className, variant, selected, asChild = false, type, ...props },
  ref,
) {
  const Comp = asChild ? Slot : "button";
  const resolvedSelected = Boolean(selected);
  return (
    <Comp
      ref={ref}
      type={asChild ? type : (type ?? "button")}
      aria-pressed={selected !== undefined ? selected : undefined}
      data-selected={resolvedSelected ? "true" : undefined}
      className={cn(chipVariants({ variant, selected: resolvedSelected }), className)}
      {...props}
    />
  );
});
