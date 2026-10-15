import type { NextConfig } from "next";

// NOUS_EXPORT=1 produce un build estático (out/) que FastAPI sirve
// empaquetado — PPR requiere runtime y no puede exportarse.
const exportando = process.env.NOUS_EXPORT === "1";

const nextConfig: NextConfig = {
  /* config options here */
  output: exportando ? "export" : undefined,
  cacheComponents: !exportando,
  partialPrefetching: !exportando,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
