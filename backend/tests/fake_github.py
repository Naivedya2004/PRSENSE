"""In-process fake of the GitHub API for tests. Routes requests by method and path via httpx.MockTransport.

No network access and no real credentials are involved. A route can hold several responses; they are
returned in order and the last one repeats. A route can also be a callable for dynamic responses.
"""
import httpx

API = "https://api.github.test"
WEB = "https://github.test"


class FakeGitHub:
    def __init__(self):
        self.routes = {}
        self.calls = []

    def route(self, method, path, *responses):
        self.routes[(method, path)] = list(responses)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        self.calls.append((request.method, request.url.path))
        queue = self.routes.get(key)
        if not queue:
            return httpx.Response(404, json={"message": "Not Found"})
        item = queue[0] if len(queue) == 1 else queue.pop(0)
        if callable(item):
            return item(request)
        status, body, headers = item
        return httpx.Response(status, json=body, headers=headers or {})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._respond)
