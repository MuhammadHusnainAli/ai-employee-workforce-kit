/** @type {import('next').NextConfig} */
const nextConfig = {
    // A self-contained server in .next/standalone: what the Docker image runs.
    output: "standalone",
    // gRPC and NATS clients run on the server only, loaded as plain Node modules.
    serverExternalPackages: ["@grpc/grpc-js", "@grpc/proto-loader", "@nats-io/transport-node"],
    // The .proto files are read at runtime: ship them with the server.
    outputFileTracingIncludes: {
        "/api/**": ["./contracts/proto/**/*"],
        "/**": ["./contracts/**/*"],
    },
    poweredByHeader: false,
};

export default nextConfig;
