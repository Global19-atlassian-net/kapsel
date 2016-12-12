# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------
# Copyright © 2016, Continuum Analytics, Inc. All rights reserved.
#
# The full license is in the file LICENSE.txt, distributed with this software.
# ----------------------------------------------------------------------------
"""Environment class representing a conda environment."""
from __future__ import absolute_import

import codecs
import difflib
import os

import conda_kapsel.internal.conda_api as conda_api
import conda_kapsel.internal.pip_api as pip_api
from conda_kapsel.internal.py2_compat import is_string

from conda_kapsel.yaml_file import _load_string, _YAMLError


def _combine_keeping_last_duplicate(items1, items2, key_func=None):
    def default_key(item):
        return item

    if key_func is None:
        key_func = default_key
    items2_keys = set([key_func(item) for item in items2])
    combined = list([item for item in items1 if key_func(item) not in items2_keys])
    combined = combined + items2
    return tuple(combined)


class EnvSpec(object):
    """Represents a set of required conda packages we could potentially instantiate as a Conda environment."""

    def __init__(self,
                 name,
                 conda_packages,
                 channels,
                 pip_packages=(),
                 description=None,
                 inherit_from_name=None,
                 inherit_from=None):
        """Construct a package set with the given name and packages.

        Args:
            name (str): name of the package set
            conda_packages (list): list of package specs to pass to conda install
            channels (list): list of channel names
            pip_packages (list): list of pip package specs to pass to pip
            description (str or None): one-sentence-ish summary of what this env is
            inherit_from_name (str or None): name of what we inherit from
            inherit_from (EnvSpec or None): pull in packages and channels from
        """
        self._name = name
        self._conda_packages = tuple(conda_packages)
        self._channels = tuple(channels)
        self._pip_packages = tuple(pip_packages)
        self._description = description
        self._channels_and_packages_hash = None
        self._inherit_from_name = inherit_from_name
        self._inherit_from = inherit_from

        # we can have only a name, that we failed to build an EnvSpec from,
        # or we can have name and EnvSpec that match.
        assert self._inherit_from is None or \
            (self._inherit_from_name is not None and
             self._inherit_from_name == self._inherit_from.name)

    @property
    def name(self):
        """Get name of the package set."""
        return self._name

    @property
    def description(self):
        """Get the description of the environment."""
        if self._description is None:
            return self._name
        else:
            return self._description

    @property
    def channels_and_packages_hash(self):
        """Get a hash of our channels and packages.

        This is used to see if they have changed. Order matters
        (change in order will count as a change).
        """
        if self._channels_and_packages_hash is None:
            import hashlib
            m = hashlib.sha1()
            for p in self.conda_packages:
                m.update(p.encode("utf-8"))
            for p in self.pip_packages:
                m.update(p.encode("utf-8"))
            for c in self.channels:
                m.update(c.encode("utf-8"))
            self._channels_and_packages_hash = m.hexdigest()
        return self._channels_and_packages_hash

    def _get_inherited(self, public_attr):
        private_attr = '_' + public_attr
        if self._inherit_from is not None:
            return _combine_keeping_last_duplicate(
                getattr(self._inherit_from, public_attr), getattr(self, private_attr))
        else:
            return getattr(self, private_attr)

    @property
    def conda_packages(self):
        """Get the conda packages to install in the environment as an iterable."""
        return self._get_inherited('conda_packages')

    @property
    def channels(self):
        """Get the channels to install conda packages from."""
        return self._get_inherited('channels')

    @property
    def pip_packages(self):
        """Get the pip packages to install in the environment as an iterable."""
        return self._get_inherited('pip_packages')

    @property
    def conda_package_names_set(self):
        """Conda package names that we require, as a Python set."""
        names = set()
        for spec in self.conda_packages:
            names.add(conda_api.parse_spec(spec).name)
        return names

    @property
    def pip_package_names_set(self):
        """Pip package names that we require, as a Python set."""
        names = set()
        for spec in self.pip_packages:
            names.add(pip_api.parse_spec(spec).name)
        return names

    @property
    def inherit_from(self):
        """Env spec that we inherit stuff from."""
        return self._inherit_from

    @property
    def inherit_from_name(self):
        """Env spec name that we inherit stuff from."""
        return self._inherit_from_name

    def path(self, project_dir):
        """The filesystem path to the default conda env containing our packages."""
        return os.path.join(project_dir, "envs", self.name)

    def diff_from(self, old):
        """A string showing the comparison between this env spec and another one."""
        channels_diff = list(difflib.ndiff(old.channels, self.channels))
        conda_diff = list(difflib.ndiff(old.conda_packages, self.conda_packages))
        pip_diff = list(difflib.ndiff(old.pip_packages, self.pip_packages))
        if pip_diff:
            pip_diff = ["  pip:"] + list(map(lambda x: "    " + x, pip_diff))
        if channels_diff:
            channels_diff = ["  channels:"] + list(map(lambda x: "    " + x, channels_diff))
        return "\n".join(channels_diff + conda_diff + pip_diff)

    def to_json(self):
        """Get JSON for a kapsel.yml env spec section."""
        packages = list(self.conda_packages)
        pip_packages = list(self.pip_packages)
        if pip_packages:
            packages.append(dict(pip=pip_packages))
        channels = list(self.channels)
        result = dict(packages=packages, channels=channels)
        if self.inherit_from_name is not None:
            result['inherit_from'] = self.inherit_from_name
        return result


def _load_environment_yml(filename):
    """Load an environment.yml as an EnvSpec, or None if not loaded."""
    try:
        with codecs.open(filename, 'r', 'utf-8') as file:
            contents = file.read()
        yaml = _load_string(contents)
    except (IOError, _YAMLError):
        return None

    name = None
    if 'name' in yaml:
        name = yaml['name']
    if not name:
        if 'prefix' in yaml and yaml['prefix']:
            name = os.path.basename(yaml['prefix'])

    if not name:
        name = os.path.basename(filename)

    # We don't do too much validation here because we end up doing it
    # later if we import this into the project, and then load it from
    # the project file. We will do the import such that we don't end up
    # keeping the new project file if it's messed up.
    #
    # However we do try to avoid crashing on None or type errors here.

    raw_dependencies = yaml.get('dependencies', [])
    if not isinstance(raw_dependencies, list):
        raw_dependencies = []

    raw_channels = yaml.get('channels', [])
    if not isinstance(raw_channels, list):
        raw_channels = []

    conda_packages = []
    pip_packages = []

    for dep in raw_dependencies:
        if is_string(dep):
            conda_packages.append(dep)
        elif isinstance(dep, dict) and 'pip' in dep and isinstance(dep['pip'], list):
            for pip_dep in dep['pip']:
                if is_string(pip_dep):
                    pip_packages.append(pip_dep)

    channels = []
    for channel in raw_channels:
        if is_string(channel):
            channels.append(channel)

    return EnvSpec(name=name, conda_packages=conda_packages, channels=channels, pip_packages=pip_packages)


def _find_out_of_sync_environment_yml_spec(project_specs, filename):
    spec = _load_environment_yml(filename)

    if spec is None:
        return None

    for existing in project_specs:
        if existing.name == spec.name and \
           existing.channels_and_packages_hash == spec.channels_and_packages_hash:
            return None

    return spec
