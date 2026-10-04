import * as React from "react";
import { cn } from "@/lib/utils";
export function Card({className,...props}:React.HTMLAttributes<HTMLDivElement>){return <div className={cn("rounded-lg border border-slate-800/90 bg-terminal-900/85 shadow-[0_12px_30px_rgba(0,0,0,.2)]",className)} {...props}/>}
export function CardHeader({className,...props}:React.HTMLAttributes<HTMLDivElement>){return <div className={cn("flex items-center justify-between border-b border-slate-800 px-4 py-3",className)} {...props}/>}
export function CardTitle({className,...props}:React.HTMLAttributes<HTMLHeadingElement>){return <h2 className={cn("text-sm font-semibold tracking-wide text-slate-100",className)} {...props}/>}
export function CardContent({className,...props}:React.HTMLAttributes<HTMLDivElement>){return <div className={cn("p-4",className)} {...props}/>}
export function CardFooter({className,...props}:React.HTMLAttributes<HTMLDivElement>){return <div className={cn("flex items-center justify-between gap-2 border-t border-slate-800/80 px-4 py-3",className)} {...props}/>}
