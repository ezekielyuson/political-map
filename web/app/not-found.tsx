import Link from "next/link";

export default function NotFound() {
  return (
    <div className="text-center py-16 space-y-4">
      <h1 className="text-2xl font-semibold text-ink">Not in the graph</h1>
      <p className="text-muted">
        That id doesn&apos;t resolve to anything we know about.
      </p>
      <Link
        href="/"
        className="inline-block rounded-md bg-accent px-4 py-2 text-white text-sm font-medium no-underline hover:bg-blue-600"
      >
        Back to search
      </Link>
    </div>
  );
}
