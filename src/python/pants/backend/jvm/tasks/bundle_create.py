# coding=utf-8
# Copyright 2014 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import (absolute_import, division, generators, nested_scopes, print_function,
                        unicode_literals, with_statement)

import os

from twitter.common.collections import OrderedSet

from pants.backend.jvm.targets.jvm_app import JvmApp
from pants.backend.jvm.targets.jvm_binary import JvmBinary
from pants.backend.jvm.tasks.classpath_util import ClasspathUtil
from pants.backend.jvm.tasks.jvm_binary_task import JvmBinaryTask
from pants.base.build_environment import get_buildroot
from pants.base.exceptions import TaskError
from pants.build_graph.target_scopes import Scopes
from pants.fs import archive
from pants.util.dirutil import safe_copy, safe_mkdir, safe_symlink
from pants.util.objects import datatype


class BundleCreate(JvmBinaryTask):
  """
  :API: public
  """

  # Directory for both internal and external libraries.
  LIBS_DIR = 'libs'
  _target_closure_kwargs = dict(include_scopes=Scopes.JVM_RUNTIME_SCOPES, respect_intransitive=True)

  @classmethod
  def register_options(cls, register):
    super(BundleCreate, cls).register_options(register)
    register('--deployjar', advanced=True, type=bool,
             fingerprint=True,
             help="Pack all 3rdparty and internal jar classfiles into a single deployjar in "
                  "the bundle's root dir. If unset, all jars will go into the bundle's libs "
                  "directory, the root will only contain a synthetic jar with its manifest's "
                  "Class-Path set to those jars. This option is also defined in jvm_app target. "
                  "Precedence is CLI option > target option > pants.ini option.")
    register('--archive', advanced=True, choices=list(archive.TYPE_NAMES),
             fingerprint=True,
             help='Create an archive of this type from the bundle. '
                  'This option is also defined in jvm_app target. '
                  'Precedence is CLI option > target option > pants.ini option.')
    register('--archive-prefix', advanced=True, type=bool,
             fingerprint=True, removal_hint='redundant option', removal_version='1.1.0',
             help='If --archive is specified, prefix archive with target basename or a unique '
                  'identifier as determined by --use-basename-prefix.')
    # `target.id` ensures global uniqueness, this flag is provided primarily for
    # backward compatibility.
    register('--use-basename-prefix', advanced=True, type=bool,
             help='Use target basename to prefix bundle folder or archive; otherwise a unique '
                  'identifier derived from target will be used.')

  @classmethod
  def product_types(cls):
    return ['jvm_bundles', 'deployable_archives']

  class App(datatype('App', ['address', 'binary', 'bundles', 'id', 'deployjar', 'archive', 'target'])):
    """A uniform interface to an app."""

    @staticmethod
    def is_app(target):
      return isinstance(target, (JvmApp, JvmBinary))

    @classmethod
    def create_app(cls, target, deployjar, archive):
      return cls(target.address,
                 target if isinstance(target, JvmBinary) else target.binary,
                 [] if isinstance(target, JvmBinary) else target.payload.bundles,
                 target.id,
                 deployjar,
                 archive,
                 target)

  @property
  def cache_target_dirs(self):
    return True

  # TODO (Benjy): The following CLI > target > config logic
  # should be implemented in the options system.
  # https://github.com/pantsbuild/pants/issues/3538
  def _resolved_option(self, target, key):
    """Get value for option "key".

    Resolution precedence is CLI option > target option > pants.ini option.
    """
    option_value = self.get_options().get(key)
    if not isinstance(target, JvmApp) or self.get_options().is_flagged(key):
      return option_value
    v = target.payload.get_field_value(key, None)
    return option_value if v is None else v

  def execute(self):
    # NB(peiyu): performance hack to convert loose directories in classpath into jars. This is
    # more efficient than loading them as individual files.
    runtime_classpath = self.context.products.get_data('runtime_classpath')
    targets_to_consolidate = self.find_consolidate_classpath_candidates(
      runtime_classpath,
      self.context.targets(**self._target_closure_kwargs),
    )
    self.consolidate_classpath(targets_to_consolidate, runtime_classpath)

    targets_to_bundle = self.context.targets(self.App.is_app)

    if self.get_options().use_basename_prefix:
      self.check_basename_conflicts([t for t in self.context.target_roots if t in targets_to_bundle])

    with self.invalidated(targets_to_bundle, invalidate_dependents=True) as invalidation_check:
      jvm_bundles_product = self.context.products.get('jvm_bundles')
      bundle_archive_product = self.context.products.get('deployable_archives')
      for vt in invalidation_check.all_vts:
        app = self.App.create_app(vt.target,
                                  self._resolved_option(vt.target, 'deployjar'),
                                  self._resolved_option(vt.target, 'archive'))

        archiver = archive.archiver(app.archive) if app.archive else None

        bundle_dir = self.bundle(app, vt.results_dir)
        # NB(Eric Ayers): Note that this product is not housed/controlled under .pants.d/  Since
        # the bundle is re-created every time, this shouldn't cause a problem, but if we ever
        # expect the product to be cached, a user running an 'rm' on the dist/ directory could
        # cause inconsistencies.
        jvm_bundles_product.add(app.target, os.path.dirname(bundle_dir)).append(os.path.basename(bundle_dir))

        archivepath = ''
        if archiver:
          archivepath = archiver.create(
            bundle_dir,
            vt.results_dir,
            app.id
          )
          bundle_archive_product.add(app.target, os.path.dirname(archivepath)).append(os.path.basename(archivepath))
          self.context.log.debug('created {}'.format(os.path.relpath(archivepath, get_buildroot())))

        # For root targets, create symlink.
        if vt.target in self.context.target_roots:
          name = vt.target.basename if self.get_options().use_basename_prefix else app.id
          bundle_symlink = os.path.join(self.get_options().pants_distdir, '{}-bundle'.format(name))
          safe_symlink(bundle_dir, bundle_symlink)
          self.context.log.info('created bundle symlink {}'.format(os.path.relpath(bundle_symlink, get_buildroot())))

          if archive and archivepath:
            archive_copy = os.path.join(self.get_options().pants_distdir, '{}.{}'.format(name, app.archive))
            safe_copy(archivepath, archive_copy, overwrite=True)
            self.context.log.info('created archive copy {}'.format(os.path.relpath(archive_copy, get_buildroot())))

  class BasenameConflictError(TaskError):
    """Indicates the same basename is used by two targets."""

  def bundle(self, app, results_dir):
    """Create a self-contained application bundle.

    The bundle will contain the target classes, dependencies and resources.
    """

    assert(isinstance(app, BundleCreate.App))

    #bundle_dir = os.path.join(self.get_options().pants_distdir, '{}-bundle'.format(app.basename))
    bundle_dir = os.path.join(results_dir, '{}-bundle'.format(app.id))
    self.context.log.debug('creating {}'.format(os.path.relpath(bundle_dir, get_buildroot())))

    safe_mkdir(bundle_dir, clean=True)

    classpath = OrderedSet()

    # Create symlinks for both internal and external dependencies under `lib_dir`. This is
    # only needed when not creating a deployjar
    lib_dir = os.path.join(bundle_dir, self.LIBS_DIR)
    if not app.deployjar:
      os.mkdir(lib_dir)
      runtime_classpath = self.context.products.get_data('runtime_classpath')
      classpath.update(ClasspathUtil.create_canonical_classpath(
        runtime_classpath,
        app.target.closure(bfs=True, **self._target_closure_kwargs),
        lib_dir,
        internal_classpath_only=False,
        excludes=app.binary.deploy_excludes,
      ))

    bundle_jar = os.path.join(bundle_dir, '{}.jar'.format(app.binary.basename))
    with self.monolithic_jar(app.binary, bundle_jar,
                             manifest_classpath=classpath) as jar:
      self.add_main_manifest_entry(jar, app.binary)

      # Make classpath complete by adding the monolithic jar.
      classpath.update([jar.path])

    if app.binary.shading_rules:
      for jar_path in classpath:
        # In case `jar_path` is a symlink, this is still safe, shaded jar will overwrite jar_path,
        # original file `jar_path` linked to remains untouched.
        # TODO run in parallel to speed up
        self.shade_jar(shading_rules=app.binary.shading_rules, jar_path=jar_path)

    for bundle in app.bundles:
      for path, relpath in bundle.filemap.items():
        bundle_path = os.path.join(bundle_dir, relpath)
        if not os.path.exists(path):
          raise TaskError('Given path: {} does not exist in target {}'.format(
            path, app.address.spec))
        safe_mkdir(os.path.dirname(bundle_path))
        os.symlink(path, bundle_path)

    return bundle_dir

  def consolidate_classpath(self, targets, classpath_products):
    """Convert loose directories in classpath_products into jars. """

    with self.invalidated(targets=targets, invalidate_dependents=True) as invalidation:
      for vt in invalidation.all_vts:
        entries = classpath_products.get_internal_classpath_entries_for_targets([vt.target])
        for index, (conf, entry) in enumerate(entries):
          if ClasspathUtil.is_dir(entry.path):
            jarpath = os.path.join(vt.results_dir, 'output-{}.jar'.format(index))

            # regenerate artifact for invalid vts
            if not vt.valid:
              with self.open_jar(jarpath, overwrite=True, compressed=False) as jar:
                jar.write(entry.path)

            # replace directory classpath entry with its jarpath
            classpath_products.remove_for_target(vt.target, [(conf, entry.path)])
            classpath_products.add_for_target(vt.target, [(conf, jarpath)])

  def find_consolidate_classpath_candidates(self, classpath_products, targets):
    targets_with_directory_in_classpath = []
    for target in targets:
      entries = classpath_products.get_internal_classpath_entries_for_targets([target])
      for conf, entry in entries:
        if ClasspathUtil.is_dir(entry.path):
          targets_with_directory_in_classpath.append(target)
          break

    return targets_with_directory_in_classpath

  def check_basename_conflicts(self, targets):
    """Apps' basenames are used as bundle directory names. Ensure they are all unique."""

    basename_seen = {}
    for target in targets:
      if target.basename in basename_seen:
        raise self.BasenameConflictError('Basename must be unique, found two targets use '
                                         "the same basename: {}'\n\t{} and \n\t{}"
                                         .format(target.basename,
                                                 basename_seen[target.basename].address.spec,
                                                 target.address.spec))
      basename_seen[target.basename] = target
