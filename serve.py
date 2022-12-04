import os
import pty
import subprocess
import sys
from select import POLLIN, poll

import flask

app = flask.Flask(__name__)
pid, fd = pty.fork()
if pid == 0:
    subprocess.call(sys.argv[1:])
    sys.exit(0)

p = poll()
p.register(fd, POLLIN)

OUTPUT = ""


@app.route("/stdin", methods=["POST"])
def stdin():
    os.write(fd, flask.request.data)
    return {"status": "ok"}


@app.route("/output")
def output():
    try:
        global OUTPUT
        if p.poll(0):
            out = os.read(fd, 1024)
            OUTPUT += out.decode()
            return {"output": out.decode(), "done": False, "whole": OUTPUT}
        return {"output": "", "done": False, "whole": OUTPUT}
    except OSError:
        return {"output": "", "done": True, "whole": OUTPUT}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
