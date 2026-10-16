import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

const config = [
  { ignores: [".next/**", ".edge-assets/**", ".wrangler/**", "node_modules/**", "next-env.d.ts"] },
  ...nextVitals,
  ...nextTypescript,
];

export default config;
