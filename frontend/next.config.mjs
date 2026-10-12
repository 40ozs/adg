/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The API base URL is read at request time from the environment so that one built
  // image can be promoted across environments.
  env: {},
  // The Render demo image (docker/Dockerfile.render) runs the self-contained server that
  // Next.js traces into .next/standalone. Every other build is unchanged.
  ...(process.env.ADG_NEXT_OUTPUT === "standalone" ? { output: "standalone" } : {}),
  // One commit, one build ID, wherever it is built: the Worker's copy of /_next/static and the
  // HTML from Render then come from the same build. Unset, Next.js picks one as usual.
  generateBuildId: async () => process.env.ADG_BUILD_ID?.trim() || null,
};

export default nextConfig;
