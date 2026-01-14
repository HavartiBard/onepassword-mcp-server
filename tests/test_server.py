import unittest

import server


class FakeSecrets:
    def __init__(self, values):
        self.values = values

    async def resolve(self, path: str):
        # Path shape: op://{vault}/{item}/{field}
        parts = path.split("/")
        try:
            _, vault, item, field = parts[0].rstrip(":"), parts[2], parts[3], parts[4]
        except IndexError:
            raise KeyError(f"Invalid path format: {path}") from None

        key = (vault, item, field)
        if key not in self.values:
            raise KeyError(f"Missing secret for {key}")
        return self.values[key]


class FakeItem:
    def __init__(self, item_id, title, vault, category):
        self.id = item_id
        self.title = title
        self.vault = type("Vault", (), {"name": vault})
        self.category = category


class FakeItems:
    def __init__(self, items):
        self.items = items
        self.created_payload = None

    def list(self, **kwargs):
        query = kwargs.get("query")
        vault = kwargs.get("vault")
        category = kwargs.get("category")

        results = self.items
        if query:
            results = [i for i in results if query.lower() in (i.title or "").lower()]
        if vault:
            results = [i for i in results if getattr(i.vault, "name", None) == vault]
        if category:
            results = [i for i in results if i.category == category]
        return results

    def create(self, payload):
        self.created_payload = payload
        return type("Result", (), {"id": "new-item-id"})


class FakeClient:
    def __init__(self, secrets_map, items):
        self.secrets = FakeSecrets(secrets_map)
        self.items = FakeItems(items)


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        secrets = {
            ("AI", "netbox", "password"): "netbox-pass",
            ("AI", "netbox", "secret"): "netbox-secret",
            ("Vault2", "api", "api_key"): "api-token-123",
        }
        items = [
            FakeItem("1", "NetBox", "AI", "LOGIN"),
            FakeItem("2", "API Token", "Vault2", "SECURE_NOTE"),
        ]
        self.fake_client = FakeClient(secrets, items)
        server._client = self.fake_client  # Reuse cached client

    async def test_resolve_secret_prefers_password(self):
        result = await server.resolve_secret_impl("netbox", intent="password", vault="AI", client=self.fake_client)
        self.assertEqual(result["field"], "password")
        self.assertEqual(result["value"], "netbox-pass")

    async def test_resolve_secret_falls_back_to_secret(self):
        result = await server.resolve_secret_impl("netbox", intent="secret", vault="AI", client=self.fake_client)
        self.assertEqual(result["field"], "secret")
        self.assertEqual(result["value"], "netbox-secret")

    async def test_resolve_secret_raises_for_missing_fields(self):
        with self.assertRaises(RuntimeError) as ctx:
            await server.resolve_secret_impl("missing-item", intent="password", vault="AI", client=self.fake_client)
        self.assertIn("Unable to resolve any fields", str(ctx.exception))

    async def test_list_items_filters_by_query_and_vault(self):
        results = await server.list_items_impl(query="api", vault="Vault2", client=self.fake_client)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "API Token")
        self.assertEqual(results[0]["vault"], "Vault2")

    async def test_upsert_item_password_template(self):
        result = await server.upsert_item_impl(
            name="New Login",
            kind="password",
            fields={"username": "user1", "password": "pass1"},
            vault="AI",
            tags=["prod"],
        )
        self.assertTrue(result["created"])
        self.assertEqual(result["vault"], "AI")
        self.assertEqual(result["fields"][0]["value"], "user1")
        self.assertEqual(result["fields"][1]["value"], "pass1")

    async def test_upsert_item_ssh_key_template(self):
        result = await server.upsert_item_impl(
            name="SSH Key",
            kind="ssh_key",
            fields={
                "private_key": "PRIVATE",
                "public_key": "PUBLIC",
                "passphrase": "secret-pass",
            },
            vault="AI",
        )
        self.assertEqual(result["kind"], "ssh_key")
        values = {f["id"]: f["value"] for f in result["fields"]}
        self.assertEqual(values["private_key"], "PRIVATE")
        self.assertEqual(values["public_key"], "PUBLIC")
        self.assertEqual(values["passphrase"], "secret-pass")


if __name__ == "__main__":
    unittest.main()
