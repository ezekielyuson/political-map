import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Political Graph Engine",
  description:
    "Browse the public US political relationship graph: politicians, PACs, donations, committee assignments, lobbying contracts.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen flex flex-col font-sans antialiased">
        <header className="border-b border-slate-200 bg-white">
          <div className="max-w-5xl mx-auto px-6 py-4 flex items-baseline gap-6">
            <Link href="/" className="font-semibold text-ink no-underline">
              Political Graph Engine
            </Link>
            <nav className="flex gap-4 text-sm text-muted">
              <Link href="/" className="hover:text-ink no-underline">
                Search
              </Link>
              <Link href="/map" className="hover:text-ink no-underline">
                Map
              </Link>
              <Link href="/paths" className="hover:text-ink no-underline">
                Paths
              </Link>
              <a
                href={`${process.env.NEXT_PUBLIC_PGE_API_URL ?? "http://localhost:8000"}/docs`}
                className="hover:text-ink no-underline"
                target="_blank"
                rel="noreferrer"
              >
                API docs
              </a>
            </nav>
          </div>
        </header>
        <main className="flex-1 max-w-5xl mx-auto w-full px-6 py-8">
          {children}
        </main>
        <footer className="border-t border-slate-200 bg-white">
          <div className="max-w-5xl mx-auto px-6 py-4 text-xs text-muted">
            Sources: FEC · Congress.gov · Senate LDA · congress-legislators.{" "}
            Every edge carries provenance + evidence type. Public political
            data; not legal or financial advice.
          </div>
        </footer>
      </body>
    </html>
  );
}
