# *****************************************************************************
#
# Copyright (c) 2020, the jupyterlab_templates authors.
#
# This file is part of the jupyterlab_templates library, distributed under the terms of
# the Apache License 2.0.  The full license can be found in the LICENSE file.
#
import json
import os
import os.path
import jupyter_core.paths
import tornado.web

from io import open
from fnmatch import fnmatch
from jupyter_client.jsonutil import json_default
from jupyter_server.base.handlers import JupyterHandler
from jupyter_server.services.contents.manager import ContentsManager
from jupyter_server.utils import url_path_join

TEMPLATES_IGNORE_FILE = ".jupyterlab_templates_ignore"


class TemplatesLoader:
    def __init__(self, template_dirs, allowed_extensions=None, template_label=None):
        self.template_dirs = template_dirs
        self.template_label = template_label or "Template"
        self.allowed_extensions = allowed_extensions or ["*.ipynb"]

    def _old(self, contents_manager: ContentsManager):
        """
        The upstream version fo this class has a single method get_templates which has the same
        implementation as this.

        But the ContentsManagerTemplatesLoader works on s3, so can't easily load all the templates (too slow).
        Hence it's useful to split loading a list of templates from loading an individual template.
        But it was easier to reuse this than to build a new one, since I don't actually use this loader.
        """
        templates = {}
        template_by_path = {}

        for path in self.template_dirs:
            # in order to produce correct filenames, abspath should point to the parent directory of path
            abspath = os.path.abspath(os.path.join(os.path.realpath(path), os.pardir))
            files = []
            # get all files in subdirectories
            for dirname, _, filenames in os.walk(path, followlinks=True):
                if dirname == path:
                    # Skip top level
                    continue

                if TEMPLATES_IGNORE_FILE in filenames:
                    # skip this very directory (subdirectories will still be considered)
                    continue

                _files = [x for x in filenames if any(fnmatch(x, y) for y in self.allowed_extensions)]
                for filename in _files:
                    if ".ipynb_checkpoints" not in dirname:
                        files.append(
                            (
                                os.path.join(dirname, filename),
                                dirname.replace(path, ""),
                                filename,
                            )
                        )
            # pull contents and push into templates list
            for f, dirname, filename in files:
                # skips over faild attempts to read content
                try:
                    with open(os.path.join(abspath, f), "r", encoding="utf8") as fp:
                        content = fp.read()
                except (FileNotFoundError, PermissionError):
                    # Can't read file, skip
                    continue

                data = {
                    "path": f,
                    "name": os.path.join(dirname, filename),
                    "dirname": dirname,
                    "filename": filename,
                    "content": content,
                }

                # remove leading slash for select
                if dirname.strip(os.path.sep) not in templates:
                    templates[dirname.strip(os.path.sep)] = []

                # don't include content unless necessary
                templates[dirname.strip(os.path.sep)].append({"name": data["name"]})

                # full data
                template_by_path[data["name"]] = data
        return templates, template_by_path

    def get_templates(self, contents_manager: ContentsManager):
        return self._old(contents_manager)[0]

    def get_template(self, path, contents_manager):
        return self._old(contents_manager)[1][path]


class ContentsManagerTemplatesLoader:
    def __init__(self, template_dirs, template_label = "Template", allowed_extensions = None, include_default = True):
        self.template_dirs = template_dirs
        self.template_label = template_label or "Template"
        self.allowed_extensions = allowed_extensions or ["*.ipynb"]

    def get_templates(self, contents_manager: ContentsManager):
        templates = {}
        template_by_path = {}

        for name in self.template_dirs:
            templates[name] = []
            dirs_to_scan = set([self.template_dirs[name]])

            while len(dirs_to_scan) > 0:
                path = dirs_to_scan.pop()
                dir_result = contents_manager.get(path, content=True, type="directory")

                for x in dir_result["content"]:
                    if x["type"] == "directory":
                        dirs_to_scan.add(x["path"])
                    elif x["type"] == "notebook":
                        templates[name].append({"name": x["path"]})

        return templates#, template_by_path

    def get_template(self, path, contents_manager):
        import json
        result = {
            "path": path,
            "name": os.path.split(path)[1],
            "dirname": os.path.split(path)[0],
            "filename": os.path.split(path)[1],
            "content": json.dumps(contents_manager.get(path, content=True, type="notebook")["content"], default=json_default)
        }
        return result


class TemplatesHandler(JupyterHandler):
    def initialize(self, loader):
        self.loader = loader

    @tornado.web.authenticated
    def get(self):
        temp = self.get_argument("template", "")
        if temp:
            self.finish(self.loader.get_template(temp, self.contents_manager))
        self.set_status(404)


class TemplateNamesHandler(JupyterHandler):
    def initialize(self, loader):
        self.loader = loader

    @tornado.web.authenticated
    def get(self):
        templates = self.loader.get_templates(self.contents_manager)
        response = {"templates": templates, "template_label": self.loader.template_label}
        self.finish(json.dumps(response))


def load_jupyter_server_extension(nb_server_app):
    """
    Called when the extension is loaded.

    Args:
        nb_server_app (NotebookWebApplication): handle to the Notebook webserver instance.
    """
    web_app = nb_server_app.web_app
    template_dirs = nb_server_app.config.get("JupyterLabTemplates", {}).get("template_dirs", [])

    local_files = nb_server_app.config.get("JupyterLabTemplates", {}).get("local_files", True)

    allowed_extensions = nb_server_app.config.get("JupyterLabTemplates", {}).get("allowed_extensions", ["*.ipynb"])

    if nb_server_app.config.get("JupyterLabTemplates", {}).get("include_default", local_files):
        template_dirs.insert(0, os.path.join(os.path.dirname(__file__), "templates"))

    base_url = web_app.settings["base_url"]

    host_pattern = ".*$"
    nb_server_app.log.info("Installing jupyterlab_templates handler on path %s" % url_path_join(base_url, "templates"))

    if nb_server_app.config.get("JupyterLabTemplates", {}).get("include_core_paths", local_files):
        template_dirs.extend([os.path.join(x, "notebook_templates") for x in jupyter_core.paths.jupyter_path()])
    nb_server_app.log.info("Search paths:\n\t%s" % "\n\t".join(template_dirs))

    template_label = nb_server_app.config.get("JupyterLabTemplates", {}).get("template_label", "Template")
    nb_server_app.log.info("Template label: %s" % template_label)


    if nb_server_app.config.get("JupyterLabTemplates", {}).get("local_files", True):
        loader = TemplatesLoader(template_dirs, allowed_extensions=allowed_extensions, template_label=template_label)
    else:
        loader = ContentsManagerTemplatesLoader(template_dirs, allowed_extensions=allowed_extensions, template_label=template_label)

    web_app.add_handlers(
        host_pattern,
        [
            (
                url_path_join(base_url, "templates/names"),
                TemplateNamesHandler,
                {"loader": loader},
            )
        ],
    )
    web_app.add_handlers(
        host_pattern,
        [
            (
                url_path_join(base_url, "templates/get"),
                TemplatesHandler,
                {"loader": loader},
            )
        ],
    )
