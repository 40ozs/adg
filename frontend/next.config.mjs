/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The API base URL is read at request time from the environment so that one built
  // image can be promoted across environments.
  env: {},
};

export default nextConfig;
