FROM node:24.8.0-alpine3.22@sha256:3e843c608bb5232f39ecb2b25e41214b958b0795914707374c8acc28487dea17 AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29.1-alpine3.22@sha256:27985295bdb22a1ef8f712863210bd5877c0f3006494a593e86b3fe0fa55467e
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
