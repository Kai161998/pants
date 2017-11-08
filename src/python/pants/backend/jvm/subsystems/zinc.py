# coding=utf-8
# Copyright 2017 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import (absolute_import, division, generators, nested_scopes, print_function,
                        unicode_literals, with_statement)

from pants.backend.jvm.subsystems.dependency_context import DependencyContext
from pants.backend.jvm.subsystems.jvm_tool_mixin import JvmToolMixin
from pants.backend.jvm.subsystems.shader import Shader
from pants.backend.jvm.targets.scala_jar_dependency import ScalaJarDependency
from pants.backend.jvm.tasks.classpath_util import ClasspathUtil
from pants.base.build_environment import get_buildroot
from pants.java.jar.jar_dependency import JarDependency
from pants.subsystem.subsystem import Subsystem
from pants.util.memo import memoized_property


class Zinc(Subsystem, JvmToolMixin):
  """Configuration for Pants' zinc wrapper tool."""

  options_scope = 'zinc'

  ZINC_COMPILE_MAIN = 'org.pantsbuild.zinc.compiler.Main'
  ZINC_EXTRACT_MAIN = 'org.pantsbuild.zinc.extractor.Main'
  DEFAULT_CONFS = ['default']

  ZINC_EXTRACTOR_TOOL_NAME = 'zinc-extractor'

  @classmethod
  def register_options(cls, register):
    super(Zinc, cls).register_options(register)
    Zinc.register_options_for(cls, register)

  @classmethod
  def subsystem_dependencies(cls):
    return super(Zinc, cls).subsystem_dependencies() + (DependencyContext,)

  @staticmethod
  def register_options_for(jvm_tool_mixin_instance, register, **kwargs):
    """Register options for the zinc tool in the context of the given JvmToolMixin.
    
    TODO: Move into the classmethod after zinc registration has been removed
    from `zinc_compile` in `1.6.0.dev0`.
    """
    cls = jvm_tool_mixin_instance

    register('--javac-plugins', advanced=True, type=list, fingerprint=True,
             help='Use these javac plugins.',
             **kwargs)
    register('--javac-plugin-args', advanced=True, type=dict, default={}, fingerprint=True,
             help='Map from javac plugin name to list of arguments for that plugin.',
             **kwargs)
    cls.register_jvm_tool(register, 'javac-plugin-dep', classpath=[],
                          help='Search for javac plugins here, as well as in any '
                               'explicit dependencies.',
                          **kwargs)

    register('--scalac-plugins', advanced=True, type=list, fingerprint=True,
             help='Use these scalac plugins.',
             **kwargs)
    register('--scalac-plugin-args', advanced=True, type=dict, default={}, fingerprint=True,
             help='Map from scalac plugin name to list of arguments for that plugin.',
             **kwargs)
    cls.register_jvm_tool(register, 'scalac-plugin-jars', classpath=[],
                          removal_version='1.5.0.dev0',
                          removal_hint='Use --compile-zinc-scalac-plugin-dep instead.')
    cls.register_jvm_tool(register, 'scalac-plugin-dep', classpath=[],
                          help='Search for scalac plugins here, as well as in any '
                               'explicit dependencies.',
                          **kwargs)

    zinc_rev = '1.0.3'

    shader_rules = [
        # The compiler-interface and compiler-bridge tool jars carry xsbt and
        # xsbti interfaces that are used across the shaded tool jar boundary so
        # we preserve these root packages wholesale along with the core scala
        # APIs.
        Shader.exclude_package('scala', recursive=True),
        Shader.exclude_package('xsbt', recursive=True),
        Shader.exclude_package('xsbti', recursive=True),
      ]

    cls.register_jvm_tool(register,
                          'zinc',
                          classpath=[
                            JarDependency('org.pantsbuild', 'zinc-compiler_2.11', '0.0.4'),
                          ],
                          **kwargs)

    cls.register_jvm_tool(register,
                          'compiler-bridge',
                          classpath=[
                            ScalaJarDependency(org='org.scala-sbt',
                                               name='compiler-bridge',
                                               rev=zinc_rev,
                                               classifier='sources',
                                               intransitive=True),
                          ],
                          **kwargs)
    cls.register_jvm_tool(register,
                          'compiler-interface',
                          classpath=[
                            JarDependency(org='org.scala-sbt', name='compiler-interface', rev=zinc_rev),
                          ],
                          # NB: We force a noop-jarjar'ing of the interface, since it is now broken
                          # up into multiple jars, but zinc does not yet support a sequence of jars
                          # for the interface.
                          main='no.such.main.Main',
                          custom_rules=shader_rules,
                          **kwargs)

    cls.register_jvm_tool(register,
                          Zinc.ZINC_EXTRACTOR_TOOL_NAME,
                          classpath=[
                            JarDependency('org.pantsbuild', 'zinc-extractor_2.11', '0.0.3')
                          ])

  def __init__(self, *args, **kwargs):
    super(Zinc, self).__init__(*args, **kwargs)
    self.set_distribution(jdk=True)

  @memoized_property
  def rebase_map_args(self):
    """We rebase known stable paths in zinc analysis to make it portable across machines."""
    rebases = {
        self.dist.real_home: '/dev/null/remapped_by_pants/java_home/',
        get_buildroot(): '/dev/null/remapped_by_pants/buildroot/',
        self.get_options().pants_workdir: '/dev/null/remapped_by_pants/workdir/',
      }
    return (
        '-rebase-map',
        ','.join('{}:{}'.format(src, dst) for src, dst in rebases.items())
      )

  @classmethod
  def _compiler_plugins_cp_entries(cls, jvm_tool_mixin_instance, products):
    """Any additional global compiletime classpath entries.

    TODO: Switch to instance memoized_property after 1.6.0.dev0.
    """
    def cp(toolname):
      scope = jvm_tool_mixin_instance.options_scope
      return jvm_tool_mixin_instance.tool_classpath_from_products(products,
                                                                  toolname,
                                                                  scope=scope)
    classpaths = cp('javac-plugin-dep') + cp('scalac-plugin-dep')
    return [(conf, jar) for conf in cls.DEFAULT_CONFS for jar in classpaths]

  def extractor_classpath(self, products):
    return self.tool_classpath_from_products(products,
                                             self.ZINC_EXTRACTOR_TOOL_NAME,
                                             scope=self.options_scope)

  def compile_classpath(self, products, classpath_product_key, target, extra_cp_entries=None):
    """Compute the compile classpath for the given target."""
    return Zinc.compile_classpath_for(self,
                                      products,
                                      classpath_product_key,
                                      target,
                                      extra_cp_entries=extra_cp_entries)

  @classmethod
  def compile_classpath_for(cls,
                            jvm_tool_mixin_instance,
                            products,
                            classpath_product_key,
                            target,
                            extra_cp_entries=None):
    """Compute the compile classpath for the given target.

    TODO: Merge with `compile_classpath` after 1.6.0.dev0.
    """
    classpath_product = products.get_data(classpath_product_key)

    if DependencyContext.global_instance().defaulted_property(target, lambda x: x.strict_deps):
      dependencies = target.strict_dependencies(DependencyContext.global_instance())
    else:
      dependencies = DependencyContext.global_instance().all_dependencies(target)

    all_extra_cp_entries = list(cls._compiler_plugins_cp_entries(jvm_tool_mixin_instance,
                                                                 products))
    if extra_cp_entries:
      all_extra_cp_entries.extend(extra_cp_entries)

    # TODO: We convert dependencies to an iterator here in order to _preserve_ a bug that will be
    # fixed in https://github.com/pantsbuild/pants/issues/4874: `ClasspathUtil.compute_classpath`
    # expects to receive a list, but had been receiving an iterator. In the context of an
    # iterator, `excludes` are not applied
    # in ClasspathProducts.get_product_target_mappings_for_targets.
    return ClasspathUtil.compute_classpath(iter(dependencies),
                                           classpath_product,
                                           all_extra_cp_entries,
                                           cls.DEFAULT_CONFS)
