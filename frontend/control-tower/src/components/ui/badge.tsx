import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium transition-colors",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "text-foreground",
        success: "border-transparent bg-success/15 text-success",
        warning: "border-transparent bg-warning/20 text-foreground",
        destructive: "border-transparent bg-destructive/15 text-destructive",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

/** A status word as a badge, coloured by what it means. */
export function StatusBadge({ status }: { status: string }) {
  const variant =
    status === "done" || status === "sent" || status === "applied" || status === "ok" || status === "approved"
      ? "success"
      : status === "awaiting_approval" || status === "pending" || status === "running" || status === "escalated"
        ? "warning"
        : status === "failed" || status === "rejected" || status === "expired"
          ? "destructive"
          : "secondary";
  return <Badge variant={variant}>{status.replace(/_/g, " ")}</Badge>;
}
