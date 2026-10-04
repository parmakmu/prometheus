import type { Metadata } from "next";
import { AgentProvider, AgentRuntime } from "@/app/agent";
import { CanvasProvider } from "@/app/canvas";

import "./globals.css";
import "@xterm/xterm/css/xterm.css";

export const metadata: Metadata = {
  title: "prometheus",
  description: "Plan and run workflows",
};

export const dynamic = "force-dynamic";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-dvh">
      <body className="h-dvh font-sans">
        <AgentProvider>
          <CanvasProvider>
            <AgentRuntime>{children}</AgentRuntime>
          </CanvasProvider>
        </AgentProvider>
      </body>
    </html>
  );
}
