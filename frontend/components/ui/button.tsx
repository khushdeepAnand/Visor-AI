import * as React from "react";
import { cn } from "@/lib/utils";
export function Button({ className, variant="default", ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & {variant?: "default"|"ghost"|"danger"}) {
  return <button className={cn("inline-flex min-h-11 items-center justify-center rounded-md border px-3 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-40", variant === "default" && "border-accent/40 bg-accent/10 text-accent hover:bg-accent/20", variant === "ghost" && "border-slate-800 bg-transparent text-slate-300 hover:bg-slate-800/60", variant === "danger" && "border-loss/40 bg-loss/10 text-loss hover:bg-loss/20", className)} {...props} />;
}
