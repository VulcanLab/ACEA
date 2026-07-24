FROM node:20-alpine AS builder

# Build-time env vars so Vite can bake the correct backend URLs
ARG VITE_API_URL=http://localhost:8800
ARG VITE_WS_URL=ws://localhost:8800/ws
ARG VITE_REPORT_URL=http://localhost:8005
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_WS_URL=$VITE_WS_URL
ENV VITE_REPORT_URL=$VITE_REPORT_URL

WORKDIR /app
COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# Production stage — serve with a lightweight static server
FROM node:20-alpine AS runner
RUN npm install -g serve
WORKDIR /app
COPY --from=builder /app/dist ./dist

EXPOSE 3030
CMD ["serve", "-s", "dist", "-l", "3030"]
