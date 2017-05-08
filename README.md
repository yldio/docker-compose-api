# Docker Compose API

```
docker run -d \                                                                
-v "/path/to/cert":"/usr/src/cert" \
-e DOCKER_CERT_PATH=/usr/src/cert \
-e DOCKER_HOST="docker/host/href" \
-p 4242:4242 \
docker-compose
```
