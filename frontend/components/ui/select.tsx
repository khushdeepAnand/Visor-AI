import * as React from "react";
import { cn } from "@/lib/utils";
export function Select({className,...props}:React.SelectHTMLAttributes<HTMLSelectElement>){return <select className={cn("h-11 rounded-md border border-slate-700 bg-terminal-950 px-3 text-sm text-slate-100 outline-none focus:border-accent/70",className)} {...props}/>}
