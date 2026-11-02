# Seguridad de Nous — modelo de amenaza y checklist

## Frontera de confianza

El único candado ante la red es la credencial Bearer:

- `NOUS_API_TOKEN` (`ns_` + 64 hex = 256 bits) → owner del workspace
  bootstrap. Es la llave maestra: no la uses en clientes ni URLs.
- `ns_u_*` → key de usuario con (usuario, workspace default, rol).
  Solo se persiste su SHA-256; revocable por prefijo. **Una key por
  cliente/integración** — si se filtra, revocas solo esa.
- `ns_inv_*` → invitación de un solo uso con expiración; se guarda
  el hash.
- `?access_token=` (RFC 6750) para clientes sin headers: viaja en la
  URL — solo tras TLS (funnel/proxy) y con una `ns_u_*` dedicada. El
  access log la enmascara como `***`.

Exposición:

- **Local** (default): `0.0.0.0:8720` en tu LAN — el token decide.
- **Tailnet** (`tailscale serve`): WireGuard cifra; solo tu tailnet
  resuelve el nodo. Recomendado para equipo.
- **Internet** (`tailscale funnel`): cualquiera llega — rota keys
  periódicamente y revisa `GET /api/calls` por llamadas ajenas.

## Matriz de autorización REST

| Rol | Alcanza |
|---|---|
| `viewer` | GET de su workspace + projects donde es miembro |
| `member` | + escritura de memoria en su ámbito |
| `owner` (workspace) | + miembros, invitaciones, projects, borrado |
| `owner` (servidor) | + `/import` (LFI/SSRF por diseño local-first), `/ai/providers` (configura endpoints salientes), `/license/*` |

Los endpoints de infraestructura son `requerir_owner` a propósito:
`/import` lee archivos del servidor y URLs arbitrarias — un member
comprometido no debe convertir al backend en lector de archivos ni
en cliente HTTP interno. Igual para `/ai/providers`: configurar un
proveedor externo es abrir un canal de salida para el contexto.

## Lo que verificamos

- Comparaciones de credenciales en tiempo constante
  (`hmac.compare_digest`), incluido el login (usuario+password
  evaluados ambos — sin oráculo de timing).
- Sin interpolación en SQL (SQLAlchemy parametrizado; las migraciones
  solo interpolan constantes).
- Keys de providers IA cifradas con pgcrypto
  (`NOUS_CLAVE_CIFRADO` o derivada del token) — nunca en claro en DB
  ni enmascaradas en API.
- Trazabilidad (`/api/calls`) no guarda headers, body ni tokens.
- Rate limit en los públicos con credencial (`/login`,
  `/invitations/redeem`): 5/min por IP real (XFF-aware, último hop)
  + techo global 30/min que acota la fuerza bruta distribuida.
- Security headers: `nosniff`, `frame-ancestors`, CSP
  `default-src 'none'` en la API.
- CORS acotado a `NOUS_CORS_ORIGINS` (localhost por defecto, sin
  credenciales).
- PostgreSQL del compose expone 5432 solo en `127.0.0.1`.
- MCP anti-DNS-rebinding con allowlist `NOUS_MCP_ALLOWED_*`.
- Scrub de secretos en llamadas salientes a LLMs (`decision/scrub.py`).

## Riesgos residuales (por diseño)

- **Prompt injection almacenada**: cualquier fuente con escritura
  puede dejar contenido con forma de instrucción que un agente lea
  después. Mitigación: `trust_source`/`untrust` baja la confianza,
  cuarentena para lo sospechoso, y `parametric`/`provenance` ponen
  techo epistémico. Trata la memoria como datos, no como órdenes.
- **`/ai/predict` member+** envía contexto al proveedor ya
  configurado — el owner decide qué proveedores existen; el member
  dispara pero no redirige.
- **Techo global del rate limit** agotable por un atacante (DoS
  breve del endpoint público) — cota elegida vs. fuerza bruta libre.
- **Token estático único** = raíz; rotarla = `.env` + restart. Las
  `ns_u_*` son el camino del día a día.
- **Funnel público**: la IP 100.x no protege más — revisa
  `/api/calls` y rota keys si expones a internet.

## Checklist antes de exponer

1. `NOUS_API_TOKEN` con 64 hex aleatorios — nunca el default.
2. Keys `ns_u_*` por cliente, rol `viewer` si solo lee, `project`
   scoped si aplica.
3. `NOUS_MCP_ALLOWED_HOSTS/ORIGINS` con solo tus nombres.
4. `NOUS_CORS_ORIGINS` sin `*`.
5. Dashboard/DB sin bind a la LAN (compose ya lo hace en 127.0.0.1).
6. Revisar `GET /api/calls` y los logs — los tokens en URL quedan
   enmascarados.
