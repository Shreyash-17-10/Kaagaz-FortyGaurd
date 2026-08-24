import type { Metadata } from "next";
import "./globals.css";

// No next/font/google: fetching Geist at build time is a network dependency that fails behind a
// firewall and takes the whole build with it. Fonts come from a system stack in globals.css.

export const metadata: Metadata = {
  title: "HeatROI — heat mitigation budget allocation",
  description:
    "Given an area, a budget and three interventions, where should the money go? " +
    "Atlanta downtown AOI, 48 census block groups.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
