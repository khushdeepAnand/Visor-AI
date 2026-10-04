import * as React from "react";
import { cn } from "@/lib/utils";
export const Input = React.forwardRef<HTMLInputElement,React.InputHTMLAttributes<HTMLInputElement>>(({className,...props},ref)=><input ref={ref} className={cn("h-11 w-full rounded-md border border-slate-700 bg-terminal-950 px-3 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-accent/70 focus:ring-1 focus:ring-accent/30",className)} {...props}/>); Input.displayName="Input";
