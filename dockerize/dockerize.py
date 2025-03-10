#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import absolute_import

import glob
import grp
import json
import logging
import os
import pwd
import shlex
import shutil
import subprocess
import tempfile

from jinja2 import Environment, PackageLoader

from .depsolver import DepSolver

LOG = logging.getLogger(__name__)


# Link handling constants
class SymlinkOptions(object):
    PRESERVE = 1
    COPY_UNSAFE = 2
    SKIP_UNSAFE = 3
    COPY_ALL = 4


class Dockerize(object):

    def __init__(self,
                 cmd=None,
                 entrypoint=None,
                 targetdir=None,
                 tag=None,
                 runtime=None,
                 buildcmd=None,
                 symlinks=SymlinkOptions.PRESERVE,
                 build=True,
                 reldir=None,
                 platform=None):

        self.lib_dirs = None
        self.docker = {}
        self.docker['runtime'] = runtime if runtime else 'docker'
        self.docker['buildcmd'] = buildcmd if buildcmd else 'build'

        if cmd:
            self.docker['cmd'] = json.dumps(shlex.split(cmd))
            LOG.debug('CMD: %s', self.docker['cmd'])
        if entrypoint:
            self.docker['entrypoint'] = json.dumps(shlex.split(entrypoint))
            LOG.debug('ENTRYPOINT: %s', self.docker['entrypoint'])
        if tag:
            self.docker['tag'] = tag
            LOG.debug('tag: %s', self.docker['tag'])

        self.targetdir = targetdir
        self.symlinks = symlinks
        self.reldir = reldir
        self.docker['platform'] = platform
        self._build_image = build

        self.users = []
        self.groups = []
        self.paths = set()
        self.env = Environment(loader=PackageLoader('dockerize', 'templates'))

    def add_user(self, user):
        '''Import a user into /etc/passwd on the image.  You can specify a
        username, in which case add_user will look up the password entry
        via getpwnam(), or you can provide a colon-delimited password
        entry, which will be used verbatim.'''

        LOG.info('adding user %s', user)
        if ':' in user:
            self.users.append(user)
        else:
            pwent = pwd.getpwnam(user)
            self.users.append(':'.join(str(x) for x in pwent))
            grent = grp.getgrgid(pwent.pw_gid)
            self.groups.append(':'.join(str(x) for x in
                                        grent[:3] + (','.join(grent[3]),)
                                        if not isinstance(x, list)))

    def add_group(self, group):
        '''Import a group into /etc/group on the image.  You can specify a
        group name, in which case add_group will look up the group entry
        via getgrnam(), or you can provide a colon-delimited group entry,
        which will be used verbatim.'''

        LOG.info('adding group %s', group)
        if ':' in group:
            self.groups.append(group)
        else:
            grent = grp.getgrnam(group)
            self.groups.append(':'.join(str(x) for x in grent))

    def add_file(self, src, dst=None):
        '''Add a file to the list of files that will be installed into the
        image.'''

        if self.reldir is not None and not src.startswith(self.reldir):
            src = os.path.join(self.reldir, src)

        if dst is None:
            if self.reldir is not None and src.startswith(self.reldir):
                dst = os.path.join(os.sep, os.path.relpath(src, self.reldir))
            else:
                dst = src

        if not dst.startswith('/'):
            raise ValueError('%s: container paths must be fully '
                             'qualified' % dst)

        self.paths.add((src, dst))

    def build(self):
        '''Call this method to produce a Docker image.  It will either
        create a temporary working directory or populate a specific
        directy, depending on the setting of targetdir.  It will populate
        this will the files you have specified via add_file and any shared
        library depdencies.  Finally, it will generate a Dockerfile and
        call "docker build" to build the image.'''

        LOG.info('start build process')
        cleanup = False
        try:
            if not self.targetdir:
                self.targetdir = tempfile.mkdtemp(prefix='dockerize')
                cleanup = True
            else:
                LOG.warning('writing output to %s', self.targetdir)
                if not os.path.isdir(self.targetdir):
                    os.mkdir(self.targetdir)

            self.copy_files()
            self.resolve_deps()
            self.populate()
            self.generate_dockerfile()
            if self._build_image:
                self.build_image()
        finally:
            if cleanup:
                shutil.rmtree(self.targetdir,
                              ignore_errors=True)

    def generate_dockerfile(self):
        LOG.info('generating Dockerfile')
        tmpl = self.env.get_template('Dockerfile')
        if self.lib_dirs is not None:
            self.docker['ld_library_path'] = ":".join(self.lib_dirs)

        with open(os.path.join(self.targetdir, '.dockerignore'), 'w') as dockerignore:
            print("/Dockerfile", file=dockerignore)
            print("/.dockerignore", file=dockerignore)

        with open(os.path.join(self.targetdir, 'Dockerfile'), 'w') as fde:
            fde.write(tmpl.render(controller=self,
                                  docker=self.docker))

    def makedirs(self, path):
        if not os.path.isdir(path):
            os.makedirs(path)

    def populate(self):
        '''Add config files to the image using built-in templates.  This is
        responsbile for creating /etc/passwd and /etc/group, among other
        files.'''

        LOG.info('populating misc config files')
        self.makedirs(os.path.join(self.targetdir, 'etc'))
        for path in ['passwd', 'group', 'nsswitch.conf']:
            tmpl = self.env.get_template(path)
            with open(os.path.join(self.targetdir, 'etc', path), 'w') as fde:
                fde.write(tmpl.render(controller=self,
                                      docker=self.docker,
                                      users=self.users,
                                      groups=self.groups))

    def copy_file(self, src, dst=None, symlinks=None):
        '''Copy a file into the image.  This uses "rsync" to perform the
        actual copy, since rsync has robust handling of directory trees and
        symlinks.'''

        if self.reldir is not None and not src.startswith(self.reldir):
            src = os.path.join(self.reldir, src)

        if dst is None:
            if self.reldir is not None and src.startswith(self.reldir):
                dst = os.path.join(os.sep, os.path.relpath(src, self.reldir))
            else:
                dst = src

        if symlinks is None:
            symlinks = self.symlinks

        LOG.info('copying file %s to %s', src, dst)
        target = os.path.join(self.targetdir, dst[1:])
        target_dir = os.path.dirname(target)
        self.makedirs(target_dir)

        cmd = ['rsync', '-a']

        # Add flag to rsync command line corresponding to the select
        # symlink handling method.
        if symlinks == SymlinkOptions.COPY_ALL:
            cmd.append('-L')
        elif symlinks == SymlinkOptions.COPY_UNSAFE:
            cmd.append('--copy-unsafe-links')
        elif symlinks == SymlinkOptions.SKIP_UNSAFE:
            cmd.append('--safe-links')

        cmd += [src, target]

        LOG.info('running: %s', cmd)
        subprocess.check_call(cmd)

    def resolve_deps(self):
        '''Uses the dockerize.depsolver.DepSolver class to find all the shared
        library dependencies of files installed into the Docker image.'''

        print("DepSolve reldir=", self.reldir)
        deps = DepSolver(reldir=self.reldir)

        # Iterate over all files in the image.
        for root, _, files in os.walk(self.targetdir):
            for name in files:
                path = os.path.join(root, name)
                deps.add(path)

        for src in deps.deps:
            self.copy_file(src, symlinks=SymlinkOptions.COPY_ALL)

        # Install some basic nss libraries to permit programs to resolve
        # users, groups, and hosts.
        lib_dirs = set()
        for libdir in deps.prefixes():
            if libdir.startswith(self.reldir):
                libdir = os.path.join(os.sep, os.path.relpath(libdir, self.reldir))
            lib_dirs.add(libdir)
        lib_dirs_prio1 = set(lib_dir for lib_dir in lib_dirs if lib_dir.startswith("/lib"))
        lib_dirs_prio2 = set(lib_dir for lib_dir in lib_dirs if lib_dir.startswith("/usr"))
        lib_dirs_prio3 = lib_dirs - lib_dirs_prio1 - lib_dirs_prio2
        self.lib_dirs = list(lib_dirs_prio3) + list(lib_dirs_prio2) + list(lib_dirs_prio1)
        for libdir in deps.prefixes():
            print("libdir: ", libdir)
            for nsslib in os.listdir(libdir):
                if nsslib.startswith('libnss') or nsslib.startswith('libresolv'):
                    src = os.path.join(libdir, nsslib)
                    LOG.info('copying %s', src)
                    self.copy_file(src, symlinks=SymlinkOptions.COPY_ALL)

    def copy_files(self):
        '''Process the list of paths generated via add_file and copy items
        into the image.'''

        for src, dst in self.paths:
            print("copy {} {}".format(src, dst))
            for srcitem in glob.iglob(src):
                self.copy_file(srcitem, dst)

    def build_image(self):
        cmd = [self.docker['runtime']] + shlex.split(self.docker['buildcmd'])
        if 'tag' in self.docker:
            cmd += ['-t', self.docker['tag']]
        cmd += [self.targetdir]

        LOG.info('building Docker image using "%s"', ' '.join(cmd))

        subprocess.check_call(cmd)
