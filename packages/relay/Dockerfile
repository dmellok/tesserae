# Self-hosted relay: the same Worker source Cloudflare runs, on Node with
# filesystem storage instead of R2. The wire contract is unchanged, so a panel
# paired against the hosted relay moves to this one by changing its base URL.
#
#   docker build -t tesserae-relay packages/relay
#   docker run -p 8787:8787 -v relay-data:/data tesserae-relay
#
# Speaks plain HTTP. Put TLS in front of it (Caddy, Traefik, nginx): the panel
# and the Tesserae install both reach it over the public internet.

FROM node:22-alpine

# No runtime dependencies: the relay is node:http plus the Web Request/Response
# Node has had globally since 18. wrangler is a devDependency and is not
# installed here, so nothing from npm ships in this image.
WORKDIR /app
COPY package.json ./
COPY src ./src

# The volume is written as the unprivileged node user.
RUN mkdir -p /data && chown -R node:node /data /app
USER node

ENV RELAY_DATA_DIR=/data \
    PORT=8787 \
    HOST=0.0.0.0

VOLUME ["/data"]
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||8787)+'/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "src/server.js"]
