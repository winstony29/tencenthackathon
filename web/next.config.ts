import type { NextConfig } from "next";

const config: NextConfig = {
  ...(process.env.NODE_ENV === "production" ? { output: "export" as const } : {}),
  agentRules: false,
  trailingSlash: true,
  skipTrailingSlashRedirect: true,
  ...(process.env.NODE_ENV === "development"
    ? {
        async rewrites() {
          return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }];
        },
      }
    : {}),
};
export default config;
