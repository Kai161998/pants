# coding=utf-8
# Copyright 2014 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import (absolute_import, division, generators, nested_scopes, print_function,
                        unicode_literals, with_statement)

from pants.util.contextutil import temporary_dir
from pants_test.pants_run_integration_test import PantsRunIntegrationTest


class CheckstyleIntegrationTest(PantsRunIntegrationTest):
  def test_checkstyle_cached(self):
    with temporary_dir(root_dir=self.workdir_root()) as cache:
      checkstyle_args = [
          'clean-all',
          'compile.checkstyle',
          "--cache-write-to=['{}']".format(cache),
          "--cache-read-from=['{}']".format(cache),
          'examples/tests/java/org/pantsbuild/example/hello/greet',
          '-ldebug'
        ]

      with temporary_dir(root_dir=self.workdir_root()) as workdir:
        pants_run = self.run_pants_with_workdir(checkstyle_args, workdir)
        self.assert_success(pants_run)
        self.assertIn('abc_Checkstyle_compile_checkstyle will write to local artifact cache',
            pants_run.stdout_data)

      with temporary_dir(root_dir=self.workdir_root()) as workdir:
        pants_run = self.run_pants_with_workdir(checkstyle_args, workdir)
        self.assert_success(pants_run)
        self.assertIn('abc_Checkstyle_compile_checkstyle will read from local artifact cache',
            pants_run.stdout_data)
