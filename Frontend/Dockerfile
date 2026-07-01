# ── Urban ABM — Frontend (multi-stage) ───────────────────────────────────────

# Stage 1: build static assets
FROM node:20-slim AS builder
WORKDIR /app
COPY Frontend/package*.json ./
RUN npm ci
COPY Frontend/ .
RUN npm run build

# Stage 2: serve with nginx
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
