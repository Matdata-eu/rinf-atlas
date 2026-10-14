# --- build stage ---
# The static bundle is the same on every architecture, so build it natively on
# the builder's platform; npm under qemu emulation crashes (illegal instruction).
FROM --platform=$BUILDPLATFORM node:26-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Shown in the About dialog; CI passes the release tag (falls back to package.json).
ARG APP_VERSION
ENV APP_VERSION=$APP_VERSION
RUN npm run build

# --- runtime stage ---
FROM nginx:1.31-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
