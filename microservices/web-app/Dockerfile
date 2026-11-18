# Build from this folder:  docker build -t web-app .
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# The standalone server: only what it needs, run as the unprivileged node user.
FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production \
    PORT=7020 \
    HOSTNAME=0.0.0.0 \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
# The .proto files the gRPC clients load at runtime.
COPY --from=build --chown=node:node /app/contracts ./contracts
USER node
EXPOSE 7020
CMD ["node", "server.js"]
