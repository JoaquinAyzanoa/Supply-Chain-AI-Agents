/** The page header every screen shares. */
export function PageTitle({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-card px-4 py-3">
      <h1 className="text-lg font-semibold">{title}</h1>
      {children}
    </div>
  );
}
