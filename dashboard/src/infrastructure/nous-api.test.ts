/**
 * Tests del adaptador HTTP de la API de Nous.
 *
 * El fetch global se sustituye por un espía que devuelve respuestas
 * controladas: se valida el contrato del adaptador (URLs, headers,
 * serialización y errores) sin tocar la red.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { crearAuth, crearClienteNous } from "./nous-api";

/** Fabrica una Response de éxito con cuerpo JSON. */
function respuestaJson(cuerpo: unknown, estado = 200): Response {
  return new Response(JSON.stringify(cuerpo), { status: estado });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("crearClienteNous", () => {
  it("listarEventos construye la URL con el rango y el Bearer token", async () => {
    const espia = vi.fn().mockResolvedValue(respuestaJson([]));
    vi.stubGlobal("fetch", espia);

    await crearClienteNous("ns_tok").listarEventos(100, 200, 50);

    const [url, init] = espia.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/eventos?");
    expect(url).toContain("desde_unix=100");
    expect(url).toContain("hasta_unix=200");
    expect(url).toContain("limite=50");
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer ns_tok",
    );
  });

  it("buscar codifica la consulta con caracteres especiales", async () => {
    const espia = vi.fn().mockResolvedValue(respuestaJson([]));
    vi.stubGlobal("fetch", espia);

    await crearClienteNous("t").buscar("estrés & ansiedad");

    const [url] = espia.mock.calls[0] as [string];
    expect(url).toContain(encodeURIComponent("estrés & ansiedad"));
  });

  it("registrarEvento envía POST JSON con fuente dashboard", async () => {
    const espia = vi
      .fn()
      .mockResolvedValue(respuestaJson({ id: "1", dimension: "cognitiva" }));
    vi.stubGlobal("fetch", espia);

    await crearClienteNous("t").registrarEvento({
      contenido: "nota",
      amplitud: 0.7,
    });

    const [, init] = espia.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    const cuerpo = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(cuerpo.fuente).toBe("dashboard");
    expect(cuerpo.contenido).toBe("nota");
  });

  it("lanza Error con el status cuando la API responde !ok", async () => {
    const espia = vi
      .fn()
      .mockResolvedValue(new Response("sin auth", { status: 401 }));
    vi.stubGlobal("fetch", espia);

    await expect(
      crearClienteNous("malo").proyeccionActual(),
    ).rejects.toThrow("Nous API 401");
  });

  it("benchmarks incluye nombre solo cuando se indica", async () => {
    // Response solo se lee una vez: fabricar uno por llamada.
    const espia = vi
      .fn()
      .mockImplementation(() => Promise.resolve(respuestaJson([])));
    vi.stubGlobal("fetch", espia);
    const cliente = crearClienteNous("t");

    await cliente.benchmarks();
    let [url] = espia.mock.calls[0] as [string];
    expect(url).not.toContain("nombre=");

    await cliente.benchmarks("suite_benchmarks");
    [url] = espia.mock.calls[1] as [string];
    expect(url).toContain("nombre=suite_benchmarks");
  });
});

describe("crearAuth", () => {
  it("login devuelve el token del cuerpo JSON", async () => {
    const espia = vi
      .fn()
      .mockResolvedValue(respuestaJson({ token: "ns_abc" }));
    vi.stubGlobal("fetch", espia);

    const token = await crearAuth().login("u", "p");

    expect(token).toBe("ns_abc");
    const [, init] = espia.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
  });

  it("login lanza cuando las credenciales fallan", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 403 })),
    );
    await expect(crearAuth().login("u", "mala")).rejects.toThrow("Login 403");
  });

  it("verificarToken refleja el status de la API", async () => {
    const espia = vi
      .fn()
      .mockResolvedValueOnce(respuestaJson([]))
      .mockResolvedValueOnce(new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", espia);
    const auth = crearAuth();

    expect(await auth.verificarToken("bueno")).toBe(true);
    expect(await auth.verificarToken("malo")).toBe(false);
  });
});
