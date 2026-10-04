import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "StockPilot AI Terminal",
    short_name: "StockPilot",
    description: "India-only market research and paper-trading terminal",
    start_url: "/",
    display: "standalone",
    background_color: "#05070d",
    theme_color: "#05070d",
    orientation: "any",
    categories: ["finance", "productivity"],
    icons: [{ src: "/icons/stockpilot.svg", sizes: "any", type: "image/svg+xml", purpose: "maskable" }],
  };
}
