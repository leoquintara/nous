/**
 * Wordmark Nous — "Nous" + punto cyan, el logotipo de la landing.
 * `compacto` muestra solo "N." para espacios reducidos (sidebar
 * colapsado).
 */
export function Logo({ compacto = false }: { compacto?: boolean }) {
  return (
    <span className="text-lg font-semibold tracking-tight">
      {compacto ? "N" : "Nous"}
      <span style={{ color: "var(--secondary)" }}>.</span>
    </span>
  );
}
