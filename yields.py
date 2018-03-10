#!/usr/bin/env python2.7

import ast
import inspect


class Get(object):
  def __init__(self, *args):
    pass


def rule():
  class RuleVisitor(ast.NodeVisitor):
    def __init__(self, parent_function):
      super(RuleVisitor, self).__init__()
      self._parent_function = parent_function

    def visit_Call(self, node):
      if node.func.id != Get.__name__:
        return

      # TODO: Validation.
      if len(node.args) == 2:
        product_type, subject_constructor = node.args
        self._parent_function._requests.append((product_type.id, subject_constructor.func.id))
      elif len(node.args) == 3:
        product_type, subject_type, _ = node.args
        self._parent_function._requests.append((product_type.id, subject_type.id))
      else:
        raise Exception('Invalid {}: {}'.format(Get.__name__, node.args))

  module_asts = {}
  def wrapper(f):
    print 'Creating @rule: `{}`'.format(f.__name__)

    f._requests = []

    caller_frame = inspect.stack()[1][0]
    caller_filename = inspect.getframeinfo(caller_frame).filename

    module_ast = module_asts.get(caller_filename)
    if module_ast is None:
      with open(caller_filename) as caller_file:
        module_asts[caller_filename] = module_ast = ast.parse(caller_file.read())
    
    for node in ast.iter_child_nodes(module_ast):
      if isinstance(node, ast.FunctionDef) and node.name == f.__name__:
        RuleVisitor(f).visit(node)

    def resolve(name):
      return caller_frame.f_globals.get(name) or caller_frame.f_builtins.get(name)

    for product_type, subject_type in f._requests:
      print '  found Get: {}, {}'.format(resolve(product_type), resolve(subject_type))
    return f
  return wrapper


@rule()
def do_this_one():
  x = yield Get(int, str("thing"))
  yield "other_thing"


def but_not_this_one():
  yield Get(int, str, "thing")


@rule()
def and_dont_get_confused_by():
  def a_nested_thing():
    return Get(int, str, "bottom")
  yield a_nested_thing()


@rule()
def with_a_loop_even():
  while True:
    yield Get(int, str("this will not end... well"))
