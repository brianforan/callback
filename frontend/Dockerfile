FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY . .
RUN npm run build

# The parser and cache tests: make test runs this stage with no network.
FROM build AS test
CMD ["npm", "test"]

FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY nginx/00-http.conf /etc/nginx/conf.d/00-http.conf
COPY nginx/local.conf /etc/nginx/conf.d/default.conf
COPY nginx/snippets /etc/nginx/snippets
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
