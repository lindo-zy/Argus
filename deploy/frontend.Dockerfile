# Argus 前端：构建静态资源后由 nginx 托管，/api 与 /mcp 同源反代到后端容器。
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# 置空 VITE_API_BASE：运行时走同源相对路径，由 nginx 反代。
RUN VITE_API_BASE= npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
