from ..service import BuildAction
from ..service import ConvergenceStrategy
from ..service import parse_repository_tag
from ..service import Service
from .. import config
from ..config.environment import Environment
from ..config.environment import split_env
from ..config.config import ConfigDetails
from ..config.config import ConfigFile
from ..config.serialize import denormalize_config
from ..const import HTTP_TIMEOUT
from ..cli import errors
from ..project import Project
from ..utils import json_hash
from ..cli.main import image_digests_for_project

from tempfile import TemporaryDirectory
from docker.auth import resolve_repository_name
from docker import APIClient
from docker.tls import TLSConfig
from threading import Thread
from os import environ
from os import path

import yaml
import requests
import zerorpc
import re



# https://github.com/docker/docker-py/blob/469b12a3c59ec344b7aaeebde05512387f5488b3/docker/utils/utils.py#L409
def client_kwargs(host="", ssl_version=None, assert_hostname=None, environment=None):
  params = {
    "version": "1.21",
    "user_agent": "docker-compose-api"
  }

  if not environment:
      environment = environ

  # empty string for cert path is the same as unset.
  cert_path = environment.get("DOCKER_CERT_PATH") or None

  # empty string for tls verify counts as "false".
  # Any value or "unset" counts as true.
  tls_verify = environment.get("DOCKER_TLS_VERIFY")
  if tls_verify == "":
      tls_verify = False
  else:
      tls_verify = tls_verify is not None
  enable_tls = cert_path or tls_verify

  timeout = environment.get("COMPOSE_HTTP_TIMEOUT") or environment.get("DOCKER_CLIENT_TIMEOUT")
  if timeout:
      params["timeout"] = int(timeout)
  else:
      params["timeout"] = HTTP_TIMEOUT

  if host:
      params["base_url"] = (
          host.replace("tcp://", "https://") if enable_tls else host
      )

  if not enable_tls:
      return params

  if not cert_path:
      cert_path = path.join(path.expanduser("~"), ".docker")

  if not tls_verify and assert_hostname is None:
      # assert_hostname is a subset of TLS verification,
      # so if it's not set already then set it to false.
      assert_hostname = False

  params["tls"] = TLSConfig(
      client_cert=(path.join(cert_path, "cert.pem"),
                   path.join(cert_path, "key.pem")),
      ca_cert=path.join(cert_path, "ca.pem"),
      verify=tls_verify,
      ssl_version=ssl_version,
      assert_hostname=assert_hostname,
  )

  return params


def get_client(host="", environment=None):
  return APIClient(**client_kwargs(
    host=host,
    ssl_version=None,
    assert_hostname=None,
    environment=environment
  ))


def get_config_details(manifest="", env_files=None, environment=None):
  return ConfigDetails(
    TemporaryDirectory().name,
    [ConfigFile(None, yaml.safe_load(manifest))],
    env_files,
    environment
  )


def get_config_data(manifest="", env_files=None, environment=None):
  config_details = get_config_details(manifest=manifest, env_files=env_files, environment=environment)
  return config.load(config_details=config_details)


def get_project(project_name=None, manifest="", host=None, config_data=None, environment=None):
  client = get_client(
    host=host,
    environment=environment
  )

  with errors.handle_connection_errors(client):
    return Project.from_config(name=project_name, config_data=config_data, client=client)


def run_in_thread(target=None, args=()):
  thread = Thread(target=target, args=args)
  thread.daemon = True
  thread.start()


# v1
# https://registry.hub.docker.com/v1/repositories/nodered/node-red-docker/tags/latest
# https://{registry}/v1/repositories/{repo_name}/tags/{tag || latest}
def get_image_id_v1(registry="index.docker.io", repo_name=None, tag="latest"):
  if tag == "":
    tag = "latest"

  r_id = requests.get("https://index.{}//v1/repositories/{}/tags/{}".format(registry, repo_name, tag))

  return r_id.text


# v2
# token = $(curl -s "https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo_name}:pull" | jq -r .token)
# curl -L -s -D - -H "Authorization: Bearer {toekn}" -H "Accept: " https://index.docker.io/v2/{repo_name}/manifests/{tag || latest}
# Etag: "sha256:2502e8213d0ce2616591d87bdcb8cda7677b800e08034c888c8dfd6b2e890ac7"
def get_image_id_v2(registry="index.docker.io", repo_name=None, tag="latest"):
  if tag == "":
    tag = "latest"

  if re.search(r"\/", repo_name) == None:
    repo_name = "library/{}".format(repo_name)

  r_token = requests.get("http://auth.{}/token?service=registry.{}&scope=repository:{}:pull".format(registry, registry, repo_name))
  r_body = r_token.json()

  r_id = requests.get("https://index.{}/v2/{}/manifests/{}".format(registry, repo_name, tag), headers={
    "Authorization": "Bearer {}".format(r_body.get("token")),
    "Accept": "application/vnd.docker.distribution.manifest.v2+json"
  })

  return r_id.json().get("config").get("digest")


def get_image_id(name):
  repository, tag, separator = parse_repository_tag(repo_path=name)
  registry, repo_name = resolve_repository_name(repo_name=repository)

  ping = requests.get("http://index.{}/v2".format(registry))
  Id = None

  if ping.status_code == 404:
    Id = get_image_id_v1(registry=registry, repo_name=repo_name, tag=tag)
  else:
    Id = get_image_id_v2(registry=registry, repo_name=repo_name, tag=tag)

  return Id


def config_dict(service=None, image_id=""):
  return {
    "options": service.options,
    "image_id": image_id,
    "links": service.get_link_names(),
    "net": service.network_mode.id,
    "networks": service.networks,
    "volumes_from": [
      (v.source.name, v.mode)
      for v in service.volumes_from if isinstance(v.source, Service)
    ]
  }


def get_environment(options_env=""):
  env = {}

  if options_env is not None:
    for line in options_env.splitlines():
      line = line.strip()
      if line and not line.startswith('#'):
          k, v = split_env(line)
          env[k] = v

  environment = Environment(env);
  environment.update(environ)
  return environment


def get_host(options=None, environment=None):
  return options.get("host") or environment.get("DOCKER_HOST")


class TopLevelCommand(object):
  def ping(self):
    return "Hello from docker-compose"

  def config(self, options=None, manifest=""):
    environment = get_environment(options_env=options.get("environment"))
    config_data = get_config_data(manifest=manifest, env_files=options.get("files"), environment=environment)
    image_digests = None
    services = []
    volumes = []

    if options.get('resolve_image_digests'):
      host = get_host(options=options, environment=environment)
      image_digests = image_digests_for_project(get_project(
        project_name=options.get("project_name"),
        manifest=manifest,
        host=host,
        config_data=config_data,
        environment=environment
      ))

    if options.get('quiet'):
      return

    if options.get('services'):
      for service in config_data.services:
        services.append(service['name'])

    if options.get('volumes'):
      for volume in config_data.volumes:
        volumes.append(volume)

    if options.get('services') or options.get('volumes'):
      return {
        "volumes": volumes,
        "services": services
      };

    return denormalize_config(config=config_data, image_digests=image_digests)


  def up(self, options=None, manifest=""):
    environment = get_environment(options_env=options.get("environment"))
    host = get_host(options=options, environment=environment)
    config_data = get_config_data(manifest=manifest, env_files=options.get("files"), environment=environment)

    project = get_project(
      project_name=options.get("project_name"),
      manifest=manifest,
      host=host,
      config_data=config_data,
      environment=environment
    )

    tree = {}

    for service in project.get_services():
      try:
        image_id = service.image()["Id"]
      except:
        try:
          image_id = get_image_id(name=service.options["image"])
        except:
          image_id = ""

      meta = config_dict(service=service, image_id=image_id)
      convergence_plan = service.convergence_plan()

      plan = {
        "action": convergence_plan.action,
        "containers": []
      }

      for container in convergence_plan.containers:
        plan["containers"].append({
          "name": container.dictionary.get("Name"),
          "id": container.dictionary.get("Id")
        })

      tree[service.name] = {
        "plan": plan,
        "hash": json_hash(obj=meta),
        "meta": meta,
        "dependencies": service.get_dependency_names(),
        "links": service.get_linked_service_names(),
        "volumes": service.get_volumes_from_names()
      }

    run_in_thread(target=project.up, args=(
      [], # service_names
      True, # start_deps
      ConvergenceStrategy.changed, # strategy
      BuildAction.none, # do_build
      options.get("timeout"), # timeout
      True, # detached
      True # remove_orphans
    ))

    return tree

  def scale(self, options=None, manifest=""):
    environment = get_environment(options_env=options.get("environment"))
    host = get_host(options=options, environment=environment)
    config_data = get_config_data(manifest=manifest, env_files=options.get("files"), environment=environment)

    project = get_project(
      project_name=options.get("project_name"),
      manifest=manifest,
      host=host,
      config_data=config_data,
      environment=environment
    )

    def do(service=None, num=0):
      service.scale(desired_num=num)

    for service in options.get("services"):
      run_in_thread(target=do, args=(
        project.get_service(service.get("name")), # service_name
        service.get("num"), # num
      ))

def main():
  server = zerorpc.Server(TopLevelCommand())

  server.bind("tcp://0.0.0.0:4242")
  print("RPC Server listenting tcp://0.0.0.0:4242")
  server.run()
