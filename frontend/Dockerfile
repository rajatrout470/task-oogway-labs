# =============================================================================
# Frontend image — multi-stage: build with Node, serve with nginx.
#
# The runtime image contains only static files and nginx: no Node, no
# node_modules, no source. Smaller and a much smaller attack surface than
# shipping a dev server.
# =============================================================================

FROM node:22-alpine AS build

WORKDIR /app

# Copy manifests first so `npm ci` is cached independently of source changes.
COPY package.json package-lock.json* ./
RUN npm ci

COPY . .

# Vite inlines env vars at BUILD time, so the API URL must be known here — it
# cannot be changed by setting an environment variable on the running container.
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build


FROM nginx:1.27-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD wget -qO- http://localhost/ >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
