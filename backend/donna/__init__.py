"""Friday: the job-search assistant inside The Office (personal scope, draft mode).

The pieces: settings and the private login (`config`), her database (`db`), the thread (`service`), a thin mail wrapper on
the mail module's leaf module (`mail`), the fetch worker (`worker`), the job sources, scoring, routing and writing, and the one sender
(`sender.py`, whose `Sender.evaluate` is the only gate that decides whether an email may go out).
"""

DONNA_VERSION = "1.0.0"          # the version of the stand-alone copy (tools/export_donna.py)
