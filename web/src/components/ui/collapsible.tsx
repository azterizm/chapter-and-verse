import * as CollapsiblePrimitive from "@radix-ui/react-collapsible";
import * as React from "react";
import { cn } from "@/lib/utils";

export const Collapsible = CollapsiblePrimitive.Root;
export const CollapsibleTrigger = CollapsiblePrimitive.Trigger;

/**
 * Collapsible content kept mounted in the DOM (`forceMount`) so anchors and
 * jump-to-source scrolling always resolve, animated via `grid-template-rows`.
 */
export const CollapsibleContent = React.forwardRef<
  React.ComponentRef<typeof CollapsiblePrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof CollapsiblePrimitive.Content>
>(function CollapsibleContent({ className, children, forceMount = true, ...props }, ref) {
  return (
    <CollapsiblePrimitive.Content
      ref={ref}
      forceMount={forceMount}
      className={cn("collapsible-content", className)}
      {...props}
    >
      <div className="overflow-hidden">{children}</div>
    </CollapsiblePrimitive.Content>
  );
});
