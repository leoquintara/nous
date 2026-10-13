---
name: nous-dashboard
description: Convenciones del dashboard Nous — Next.js 16, TypeScript, arquitectura hexagonal, liquid glass + glassmorphism, paleta Quintara. Usar al editar dashboard/.
---

# Nous Dashboard — convenciones

## Stack

- Next.js 16 (App Router) + TypeScript estricto.
- Estilos: CSS modules o Tailwind con tokens CSS custom properties.
- Iconos: Iconoir (`iconoir-react`).
- 3D: Three.js + React Three Fiber (migración del plugin DSH Memory).
- Arquitectura hexagonal: `domain/` (puros), `application/` (casos de uso),
  `infrastructure/` (adaptadores HTTP/MCP), `ui/` (componentes).

## Tema visual — liquid glass

- `backdrop-filter: blur()` + `saturate()` en tarjetas, nav, modales.
- Bordes translúcidos `rgba(255,255,255,0.18)` sobre fondos con gradiente sutil.
- Sombras suaves multicapa, radios generosos (16-24px).

## Paleta Quintara AI

| Token | Light | Dark |
|-------|-------|------|
| primary | `#198BF8` | `#4aa4fa` |
| secondary | `#00D6D4` | `#2de3e1` |
| ink | `#101050` | `#e8e8ff` |
| surface | `#f6f6f6` | `#14142b` |
| line | `#E7E7F2` | `rgba(231,231,242,0.12)` |
| danger | `#c51533` | `#ff5570` |
| success | `#00c850` | `#2edb72` |

Definir como CSS custom properties bajo `:root` y `[data-theme="dark"]`.
Nunca hardcodear hex en componentes — siempre `var(--token)`.

## Docstrings

- Cada método público lleva JSDoc **en español** (`/** Busca eventos... */`).
- Nomenclatura en español para dominio (`evento`, `dimension`), inglés para
  mecanismo técnico (`useEvents`, `fetchJson`).
