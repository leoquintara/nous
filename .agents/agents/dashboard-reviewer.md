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
