import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

export const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 text-ui whitespace-nowrap select-none cursor-pointer transition-[background-color,color,opacity,transform] duration-[var(--dur-fast)] ease-[var(--ease-out)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-40 [&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "rounded-full bg-primary text-primary-foreground hover:opacity-90 active:scale-[0.97]",
        ghost: "rounded-lg text-foreground hover:bg-primary-soft hover:text-primary active:scale-[0.98]",
        outline:
          "rounded-lg border border-border bg-surface text-foreground hover:bg-primary-soft hover:text-primary active:scale-[0.98]",
      },
      size: {
        sm: "h-8 px-3",
        md: "h-11 px-4",
        icon: "h-11 w-11",
      },
    },
    defaultVariants: {
      variant: "ghost",
      size: "md",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, asChild = false, type, ...props },
  ref,
) {
  const Comp = asChild ? Slot : "button";
  return (
    <Comp
      ref={ref}
      type={asChild ? type : (type ?? "button")}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  );
});
