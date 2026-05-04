import { MapView } from "@/components/map-view";
import { getMapPoliticians } from "@/lib/api";

export const dynamic = "force-dynamic";
export const metadata = { title: "Map — PGE" };

// Server-fetch the politicians up front so the initial paint is fast and
// SEO-friendly. Subsequent interactions (selecting a politician, drawing
// arcs to companies) happen client-side.
export default async function MapPage() {
  let politicians = [] as Awaited<
    ReturnType<typeof getMapPoliticians>
  >["politicians"];
  let error: string | null = null;
  try {
    const res = await getMapPoliticians();
    politicians = res.politicians;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold text-ink">
          Map of US Congress
        </h1>
        <p className="text-sm text-muted max-w-2xl">
          {politicians.length} politicians shown at their state capital. Click a
          dot to surface the corporate PACs that have donated to them — lines
          will connect to each company&apos;s headquarters.
        </p>
      </div>

      {error ? (
        <div className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-800">
          Couldn&apos;t reach the API ({error}).
        </div>
      ) : (
        <MapView politicians={politicians} />
      )}
    </div>
  );
}
