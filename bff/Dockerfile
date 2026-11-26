FROM node:22-slim

WORKDIR /app

# See runtime/Dockerfile for why.
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

COPY package.json .
RUN npm install --omit=dev

COPY src ./src

EXPOSE 4000

CMD ["node", "src/bootstrap.js"]
