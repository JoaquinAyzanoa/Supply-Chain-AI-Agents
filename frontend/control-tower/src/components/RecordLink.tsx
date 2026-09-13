/**
 * A link to a place in the Control Tower given as a path with a query string, the way
 * the director cites records ("/approvals?id=17", "/?po=P00077", "/risk"). Stays inside
 * the app router so nothing reloads.
 */
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

export function RecordLink({ path, children, className }: { path: string; children: ReactNode; className?: string }) {
  const [pathname, query] = path.split("?");
  // the router serialises search values as JSON: numbers must travel as numbers ("id=17")
  const search = query ? Object.fromEntries([...new URLSearchParams(query)].map(([key, value]) => [key, /^\d+$/.test(value) ? Number(value) : value])) : {};
  return (
    <Link to={(pathname || "/") as "/"} search={search as never} className={className}>
      {children}
    </Link>
  );
}
