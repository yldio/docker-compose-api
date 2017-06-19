FROM python:3-alpine

ENV PYTHONUNBUFFERED 0
# Get and configure containerpilot
ENV CP_SHA1 6da4a4ab3dd92d8fd009cdb81a4d4002a90c8b7c
ENV CONTAINERPILOT_VERSION 3.0.0
ENV CONTAINERPILOT /etc/containerpilot.json

RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app

RUN set -x \
 && apk update \
 && apk add --update curl libzmq linux-headers build-base musl-dev \
 && apk upgrade \
 && rm -rf /var/cache/apk/* \
 && curl -Lo /tmp/containerpilot.tar.gz "https://github.com/joyent/containerpilot/releases/download/${CONTAINERPILOT_VERSION}/containerpilot-${CONTAINERPILOT_VERSION}.tar.gz" \
 && echo "${CP_SHA1}  /tmp/containerpilot.tar.gz" | sha1sum -c \
 && tar zxf /tmp/containerpilot.tar.gz -C /bin \
 && rm /tmp/containerpilot.tar.gz

COPY requirements.txt /usr/src/app/
RUN pip install --no-cache-dir -r requirements.txt

COPY ./etc/containerpilot.json /etc/
COPY . /usr/src/app

EXPOSE 4242
CMD ["/bin/containerpilot", "python", "-u", "./bin/docker-compose"]
