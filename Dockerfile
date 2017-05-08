FROM python:3-alpine

ENV PYTHONUNBUFFERED 0

RUN apk update && apk add libzmq linux-headers build-base musl-dev

RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app

COPY requirements.txt /usr/src/app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /usr/src/app

CMD ["python", "-u", "./bin/docker-compose"]
