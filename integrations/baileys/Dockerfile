FROM node:22-alpine

WORKDIR /app

COPY package.json ./

RUN npm install --omit=dev

COPY index.js ./

RUN mkdir -p /app/auth

CMD ["npm", "start"]
