FROM node:20-alpine

WORKDIR /app

COPY package.json ./

COPY src ./src

ENV PORT=8080
EXPOSE 8080

CMD ["npm", "start"]
