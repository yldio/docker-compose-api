# Docker Compose API

## build

```
docker build -t docker-compose-api . 
```

## run

**local**:

```
docker run -p 4242:4242 -d docker-compose-api
```

**remote**:
```
docker run \
-v "/local/path/to/docker/cert":"/usr/src/cert" \
-e DOCKER_CERT_PATH=/usr/src/cert \
-e DOCKER_HOST="http://us-sw-1.docker.joyent.com:2376" \
-e DOCKER_CLIENT_TIMEOUT=300 \
-e COMPOSE_HTTP_TIMEOUT=300 \
-p 4242:4242 \
-d \
docker-compose-api
```
