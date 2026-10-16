# Build from the repository root: docker build -f web/Dockerfile .
FROM node:22-alpine AS build
WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/index.html web/tsconfig.json web/vite.config.ts ./
COPY web/src ./src
COPY web/public ./public
ARG VITE_API_MODE=live
ARG VITE_API_URL=
ENV VITE_API_MODE=${VITE_API_MODE} \
    VITE_API_URL=${VITE_API_URL}
RUN npm run build

FROM nginxinc/nginx-unprivileged:stable-alpine
# ECS containers in one task share localhost. Compose overrides this address.
ENV API_UPSTREAM=http://127.0.0.1:8000
COPY web/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
