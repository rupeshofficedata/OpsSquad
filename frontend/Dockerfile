FROM node:22-slim

WORKDIR /app

# See runtime/Dockerfile for why.
ARG GIT_SHA=unknown
ENV VITE_GIT_SHA=$GIT_SHA

COPY package.json .
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
