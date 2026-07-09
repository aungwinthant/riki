"""A mock server that perfectly implements the sample OpenAPI spec for testing."""
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

DATA: dict = {
    "pets": [],
    "users": [],
    "next_pet_id": 1,
    "next_user_id": 1,
}


class MockAPIHandler(BaseHTTPRequestHandler):
    def _path(self):
        return urlparse(self.path).path

    def do_GET(self):
        path = self._path()
        if path == "/pets":
            self._list_pets()
        elif path.startswith("/pets/"):
            self._get_pet()
        elif path == "/users":
            self._list_users()
        elif path.startswith("/users/"):
            self._get_user()
        else:
            self.send_error(404)

    def do_POST(self):
        path = self._path()
        if path == "/pets":
            self._create_pet()
        elif path == "/users":
            self._create_user()
        else:
            self.send_error(404)

    def do_DELETE(self):
        path = self._path()
        if path.startswith("/pets/"):
            self._delete_pet()
        elif path.startswith("/users/"):
            self._delete_user()
        else:
            self.send_error(404)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length > 0 else {}

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_no_content(self):
        self.send_response(204)
        self.end_headers()

    def _get_id_from_path(self):
        parts = self._path().split("/")
        return int(parts[-1])

    def _list_pets(self):
        self._send_json(DATA["pets"])

    def _list_users(self):
        self._send_json(DATA["users"])

    def _get_pet(self):
        pet_id = self._get_id_from_path()
        for pet in DATA["pets"]:
            if pet["id"] == pet_id:
                self._send_json(pet)
                return
        self.send_error(404)

    def _get_user(self):
        user_id = self._get_id_from_path()
        for user in DATA["users"]:
            if user["id"] == user_id:
                self._send_json(user)
                return
        self.send_error(404)

    def _create_pet(self):
        body = self._read_body()
        pet_id = DATA["next_pet_id"]
        DATA["next_pet_id"] += 1
        pet = {
            "id": pet_id,
            "name": body.get("name", "unknown"),
            "species": body.get("species", "cat"),
            "age": body.get("age", 1),
        }
        if "ownerId" in body:
            pet["ownerId"] = body["ownerId"]
        DATA["pets"].append(pet)
        self._send_json(pet, status=201)

    def _create_user(self):
        body = self._read_body()
        user_id = DATA["next_user_id"]
        DATA["next_user_id"] += 1
        user = {
            "id": user_id,
            "name": body.get("name", "unknown"),
            "email": body.get("email", "unknown@example.com"),
        }
        DATA["users"].append(user)
        self._send_json(user, status=201)

    def _delete_pet(self):
        pet_id = self._get_id_from_path()
        for i, pet in enumerate(DATA["pets"]):
            if pet["id"] == pet_id:
                DATA["pets"].pop(i)
                self._send_no_content()
                return
        self.send_error(404)

    def _delete_user(self):
        user_id = self._get_id_from_path()
        for i, user in enumerate(DATA["users"]):
            if user["id"] == user_id:
                DATA["users"].pop(i)
                self._send_no_content()
                return
        self.send_error(404)


def run_mock_server(port=8765):
    server = HTTPServer(("0.0.0.0", port), MockAPIHandler)
    print(f"Mock API running on http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_mock_server()