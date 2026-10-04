import Script from "next/script";
import "react-mosaic-component/react-mosaic-component.css";
import "./globals.css";
import { Providers } from "@/components/Providers";
import { MotionTokenStyles } from "@/components/MotionTokenStyles";
import { StatusBanner } from "@/components/StatusBanner";
import { ThemeProvider } from "@/components/ThemeProvider";

export const metadata = { title: "StockPilot AI Terminal", description: "India-only AI market research and paper-trading terminal", manifest: "/manifest.webmanifest" };

// Ledger design system (v10): warm paper by default, with a full dark
// variant defined as `html.dark` in globals.css. Which one is active is
// decided by ThemeProvider/ThemeToggle (components/ThemeProvider.tsx), not
// hardcoded here. themeColor tracks the OS preference so the browser chrome
// (address bar, PWA splash) matches whichever palette paints first.
export const viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f1e8" },
    { media: "(prefers-color-scheme: dark)", color: "#100e0a" },
  ],
};

// Runs before hydration so the right theme class is on <html> for the very
// first paint - no flash of the wrong palette. Reads the same "stockpilot-theme"
// localStorage key ThemeProvider writes to; falls back to the OS preference;
// defaults to light (the app's original design) if neither is available.
const THEME_INIT_SCRIPT =
  '(function(){try{var s=localStorage.getItem("stockpilot-theme");' +
  'var m=window.matchMedia("(prefers-color-scheme: dark)").matches;' +
  'var dark=s==="dark"||((s!=="light"&&s!=="dark")&&m);' +
  'document.documentElement.classList.toggle("dark",dark);}catch(e){}})();';

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <Script id="theme-init" strategy="beforeInteractive">{THEME_INIT_SCRIPT}</Script>
        <MotionTokenStyles />
        <ThemeProvider>
          <Providers>
            <StatusBanner />
            {children}
          </Providers>
        </ThemeProvider>
      </body>
    </html>
  );
}
