import type { NextConfig } from "next";

// The browser must reach the API on its own origin so the host-only session
// cookie is sent with every request. Without this proxy the UI runs on
// 127.0.0.1:3000 while calling localhost:8000, the cookie is not attached, and
// every protected workspace renders "Sign in required" right after a successful
// login.
const apiOrigin = process.env.NEXT_PUBLIC_API_ORIGIN || "http://127.0.0.1:8000";
const devEval = process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : "";
const securityHeaders = [
  { key: "Content-Security-Policy", value: `default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; form-action 'self'; img-src 'self' data: blob:; font-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'${devEval}; connect-src 'self' ws: wss: http://127.0.0.1:8000 http://localhost:8000` },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
];

const nextConfig: NextConfig = {
  distDir: process.env.STOCKPILOT_NEXT_DIST_DIR || ".next",
  reactStrictMode: true,
  poweredByHeader: false,
  turbopack: {
    root: import.meta.dirname,
  },
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${apiOrigin}/api/v1/:path*` },
      { source: "/openapi.json", destination: `${apiOrigin}/openapi.json` },
      { source: "/health", destination: `${apiOrigin}/health` },
    ];
  },
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
};
export default nextConfig;
