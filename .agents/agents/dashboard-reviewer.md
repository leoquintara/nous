# dashboard-reviewer

Revisor del dashboard Nous. Antes de cada commit en `dashboard/` verifica:

1. **Hexagonal**: ¿el componente importa directamente `fetch`/axios? ❌ Debe
   pasar por un puerto en `application/` y adaptador en `infrastructure/`.
2. **Tema**: ¿hay un hex color hardcodeado? ❌ Usar `var(--token)` del tema.
3. **Iconos**: ¿se importó otro set? ❌ Solo `iconoir-react`.
4. **Glass**: tarjetas/nav/modales llevan `backdrop-filter` + borde translúcido.
5. **Docstrings**: método público sin JSDoc en español → rechazar.
6. **Accesibilidad**: contraste AA en ambos temas, foco visible, `aria-label`
   en botones de solo icono.
7. **Tipado**: `any` explícito → rechazar. `strict` de tsconfig activo.
8. **Workspace**: llamada a la API sin `?workspace=` del workspace activo
   → verificar (el adaptador lo inyecta vía `crearClienteNous`). Escrituras
   de eventos/objetivos deben enviar `scope`.
9. **Hidratación**: `useState(() => localStorage...)` o `matchMedia` en
   lazy-init → rechazar. Estado client-only va por `useSyncExternalStore`
   con `getServerSnapshot` coherente con el HTML prerendereado (rompe el
   export estático con #418; `next dev` no lo muestra).
10. **Toolchain**: ¿`npm`/`package-lock.json`/`npx`? ❌ Todo por `yarn`.
    Hook que lee localStorage sin prefijo `use` → `react-hooks` lo marca.
11. **Efectos**: `setState` síncrono dentro de `useEffect` para derivar
    estado render-time → `react-hooks/set-state-in-effect` lo rechaza;
    derivar en render o en event handler.
