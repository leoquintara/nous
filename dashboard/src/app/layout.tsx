import type { Metadata } from "next";

import "./globals.css";
import { ProveedorApp } from "@/ui/proveedor-app";

export const metadata: Metadata = {
  title: "Nous — Memoria 4D",
  description: "Dashboard de la memoria persistente 4D de Nous.",
};

/** Layout raíz: envuelve la app en el proveedor de tema/auth. */
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es" suppressHydrationWarning>
      <body>
        <ProveedorApp>{children}</ProveedorApp>
      </body>
    </html>
  );
}
