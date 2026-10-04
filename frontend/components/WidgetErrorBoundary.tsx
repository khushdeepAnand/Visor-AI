"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";
import { RotateCcw, TriangleAlert } from "lucide-react";

export class WidgetErrorBoundary extends Component<{ children: ReactNode; title?: string }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    // The route remains usable; production error reporting can observe this boundary.
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div role="alert" className="grid min-h-52 place-items-center bg-terminal-950 p-5 text-center">
        <div>
          <TriangleAlert aria-hidden="true" className="mx-auto text-warning" size={24} />
          <h3 className="mt-3 text-sm font-semibold text-white">{this.props.title || "Panel unavailable"}</h3>
          <p className="mt-1 text-xs text-slate-500">The rest of your workspace is still available.</p>
          <button type="button" onClick={() => this.setState({ failed: false })} className="mt-4 inline-flex min-h-11 items-center gap-2 rounded-lg border border-slate-700 px-3 text-xs text-slate-300">
            <RotateCcw aria-hidden="true" size={14} />Retry panel
          </button>
        </div>
      </div>
    );
  }
}
