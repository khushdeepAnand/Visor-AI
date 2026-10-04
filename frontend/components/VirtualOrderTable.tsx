"use client";

import { useMemo, useRef } from "react";
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { inr } from "@/lib/utils";

export type PaperOrderRow = {
  id: number;
  symbol: string;
  side: "BUY" | "SELL";
  quantity: number;
  price: number;
  status: string;
  order_type: string;
  instrument_type?: string;
  lot_size?: number;
};

const helper = createColumnHelper<PaperOrderRow>();

export function VirtualOrderTable({ orders }: { orders: PaperOrderRow[] }) {
  const columns = useMemo(() => [
    helper.accessor("id", { header: "#", cell: (info) => info.getValue() }),
    helper.accessor("symbol", { header: "Instrument", cell: (info) => <span className="font-semibold text-slate-200">{info.getValue()}</span> }),
    helper.accessor("side", { header: "Side", cell: (info) => <span className={info.getValue() === "BUY" ? "signal-up" : "signal-down"}>{info.getValue()}</span> }),
    helper.accessor("order_type", { header: "Order", cell: (info) => info.getValue() }),
    helper.accessor("status", { header: "Status", cell: (info) => info.getValue() }),
    helper.accessor("price", { header: "Fill / mark", cell: (info) => <span className="tabular">{inr(info.getValue())}</span> }),
  ], []);
  const table = useReactTable({ data: orders, columns, getCoreRowModel: getCoreRowModel() });
  const rows = table.getRowModel().rows;
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({ count: rows.length, getScrollElement: () => scrollRef.current, estimateSize: () => 38, overscan: 8 });

  return (
    <div className="text-xs" data-testid="virtual-order-table">
      <div className="grid grid-cols-[48px_minmax(150px,1fr)_70px_110px_90px_120px] border-b border-slate-800 px-4 py-2 text-[9px] uppercase tracking-wider text-slate-600">
        {table.getHeaderGroups()[0]?.headers.map((header) => <div key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</div>)}
      </div>
      <div ref={scrollRef} className="relative h-[360px] overflow-auto">
        <div style={{ height: `${virtualizer.getTotalSize()}px`, position: "relative" }}>
          {virtualizer.getVirtualItems().map((virtualRow) => {
            const row = rows[virtualRow.index];
            return <div key={row.id} className="interactive-surface absolute left-0 top-0 grid w-full grid-cols-[48px_minmax(150px,1fr)_70px_110px_90px_120px] items-center border-b border-slate-900 px-4 text-slate-400 hover:bg-accent/5" style={{ height: `${virtualRow.size}px`, transform: `translateY(${virtualRow.start}px)` }}>{row.getVisibleCells().map((cell) => <div key={cell.id} className="truncate pr-2">{flexRender(cell.column.columnDef.cell, cell.getContext())}</div>)}</div>;
          })}
        </div>
      </div>
      {!orders.length && <div className="p-5 text-center text-slate-600">No paper orders yet.</div>}
    </div>
  );
}
