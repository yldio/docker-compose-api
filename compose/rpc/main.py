from ..service import BuildAction
from ..service import ConvergenceStrategy
from ..service import parse_repository_tag
from ..service import Service
from .. import config
from ..config.config import ConfigDetails
from ..config.config import ConfigFile
from ..const import HTTP_TIMEOUT
from ..cli import errors
from ..project import Project
from ..utils import json_hash

from tempfile import TemporaryDirectory
from docker.auth import resolve_repository_name
from docker import APIClient
from threading import Thread

import yaml
import requests
import zerorpc
import re

def get_client(host="", tls_config=None, timeout=None, user_agent=""):
  if timeout:
      tout = int(timeout)
  else:
      tout = HTTP_TIMEOUT

  return APIClient(
    base_url=host,
    version="1.21",
    timeout=tout,
    tls=tls_config,
    user_agent=user_agent
  )

def get_config_details(manifest=""):
  return ConfigDetails(
      TemporaryDirectory().name,
      [ConfigFile(None, yaml.safe_load(manifest))],
      None
  )

def get_project(project_name=None, manifest="", verbose=True,
                host=None, tls_config=None, environment=None):

    config_details = get_config_details(manifest)
    config_data = config.load(config_details)

    client = get_client(
      host=host,
      tls_config=tls_config,
      timeout=None,
      user_agent=None
    )

    with errors.handle_connection_errors(client):
        return Project.from_config(project_name, config_data, client)

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
  repository, tag, separator = parse_repository_tag(name)
  registry, repo_name = resolve_repository_name(repository)

  ping = requests.get("http://index.{}/v2".format(registry))
  Id = None

  if ping.status_code == 404:
    Id = get_image_id_v1(registry, repo_name, tag)
  else:
    Id = get_image_id_v2(registry, repo_name, tag)

  return Id

def config_dict(service, image_id):
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

class TopLevelCommand(object):
  def up(self, options, manifest=""):
    start_deps = True
    detached = True
    remove_orphans = True
    # exit_value_from = None
    # cascade_stop = False
    service_names = []
    timeout = None
    environment = {}
    host = None # "http+docker:///var/run/docker.sock" # project.host
    tree = {}

    project = get_project(
      options.get("project_name"),
      manifest,
      True,
      host,
      None, # tls_config_from_options(options),
      environment
    )

    for service in project.get_services():
      try:
        image_id = service.image()['Id']
      except:
        image_id = get_image_id(service.options["image"])

      meta = config_dict(service, image_id)
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
        "hash": json_hash(meta),
        "meta": meta,
        "dependencies": service.get_dependency_names(),
        "links": service.get_linked_service_names(),
        "volumes": service.get_volumes_from_names()
      }

    run_in_thread(target=project.up, args=(
      service_names,
      start_deps,
      ConvergenceStrategy.changed,
      BuildAction.none,
      timeout,
      detached,
      remove_orphans
    ))

    return tree

def main():
  server = zerorpc.Server(TopLevelCommand())
  server.bind("tcp://0.0.0.0:4242")
  server.run()
