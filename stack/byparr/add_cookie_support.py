"""Teach Byparr to honour the FlareSolverr `cookies` request parameter.

Byparr exposes `cookies` on its *response* model (Solution) only. LinkRequest
has no such field, so cookies sent by a client are dropped by pydantic and every
request goes out anonymous - which makes cookie-authenticated trackers such as
TorrentBD impossible to reach.

Two edits are needed:
  1. models.py    - accept `cookies` on LinkRequest.
  2. endpoints.py - seed the browser context with them before navigating.

Upstream: https://github.com/ThePhaseless/Byparr
"""
import pathlib
import sys

SRC = pathlib.Path("/app/src")


def patch(path, anchor, addition, marker):
    text = path.read_text(encoding="utf-8")
    if marker in text:
        print(f"{path.name}: already patched")
        return
    if anchor not in text:
        sys.exit(f"{path.name}: anchor not found; Byparr layout changed, patch needs review")
    path.write_text(text.replace(anchor, anchor + addition, 1), encoding="utf-8")
    print(f"{path.name}: patched")


patch(
    SRC / "models.py",
    'class LinkRequest(BaseModel):\n    model_config = {"populate_by_name": True}\n',
    '''
    # FlareSolverr clients send cookies here to authenticate the request.
    cookies: list[Cookie] = []
''',
    marker="cookies: list[Cookie] = []\n\n    cmd",
)

patch(
    SRC / "endpoints.py",
    "    await setup_routes(request, dep)\n",
    '''
    # Seed the browser context before navigating, otherwise an authenticated
    # request is indistinguishable from an anonymous one.
    if request.cookies:
        await dep.context.add_cookies(request.cookies)  # type: ignore[arg-type]
''',
    marker="add_cookies(request.cookies)",
)
