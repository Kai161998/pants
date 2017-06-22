from pants.util.dirutil import rm_rf

from flask import Flask


app = Flask(__name__)


@app.route('/')
def hello():
  return 'hello'


if __name__ == '__main__':
  app.run(use_reloader=True)