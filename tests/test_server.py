import os
import stat
import tempfile
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

    async def test_run_with_secrets_injects_env_vars(self):
        """Verify secrets are injected as environment variables."""
        result = await server.run_with_secrets_impl(
            command=["python", "-c", "import os; print(os.environ.get('DB_PASS', 'NOT_SET'))"],
            secrets=[{"item": "netbox", "intent": "password", "env": "DB_PASS"}],
            vault="AI",
            client=self.fake_client,
        )
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("netbox-pass", result["stdout"])
        self.assertEqual(result["secrets_injected"], ["DB_PASS"])

    async def test_run_with_secrets_does_not_leak_secrets_in_return(self):
        """Verify secrets are not in the return value (only env var names)."""
        result = await server.run_with_secrets_impl(
            command=["echo", "hello"],
            secrets=[{"item": "netbox", "intent": "password", "env": "SECRET_VAR"}],
            vault="AI",
            client=self.fake_client,
        )
        # The actual secret value should NOT appear in the result dict
        result_str = str(result)
        self.assertNotIn("netbox-pass", result_str)
        # But the env var name should be listed
        self.assertIn("SECRET_VAR", result["secrets_injected"])

    async def test_run_with_secrets_timeout(self):
        """Verify timeout kills long-running processes."""
        result = await server.run_with_secrets_impl(
            command=["sleep", "10"],
            secrets=[],
            vault="AI",
            timeout=1,
            client=self.fake_client,
        )
        self.assertEqual(result["exit_code"], -1)
        self.assertTrue(result.get("timed_out", False))

    async def test_write_env_file_dotenv_format(self):
        """Test writing secrets in dotenv format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "secrets.env")
            result = await server.write_env_file_impl(
                path=path,
                secrets=[{"item": "netbox", "intent": "password", "key": "DB_PASS"}],
                vault="AI",
                format="dotenv",
                client=self.fake_client,
            )
            self.assertEqual(result["path"], path)
            self.assertEqual(result["format"], "dotenv")
            self.assertEqual(result["keys"], ["DB_PASS"])
            self.assertEqual(result["permissions"], "0600")

            # Verify file content
            with open(path) as f:
                content = f.read()
            self.assertIn('DB_PASS="netbox-pass"', content)

            # Verify permissions (0600)
            file_stat = os.stat(path)
            self.assertEqual(stat.S_IMODE(file_stat.st_mode), 0o600)

    async def test_write_env_file_export_format(self):
        """Test writing secrets in export format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "secrets.sh")
            result = await server.write_env_file_impl(
                path=path,
                secrets=[{"item": "netbox", "intent": "password", "key": "DB_PASS"}],
                vault="AI",
                format="export",
                client=self.fake_client,
            )
            with open(path) as f:
                content = f.read()
            self.assertIn('export DB_PASS="netbox-pass"', content)

    async def test_write_env_file_json_format(self):
        """Test writing secrets in JSON format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "secrets.json")
            result = await server.write_env_file_impl(
                path=path,
                secrets=[{"item": "netbox", "intent": "password", "key": "DB_PASS"}],
                vault="AI",
                format="json",
                client=self.fake_client,
            )
            with open(path) as f:
                content = f.read()
            import json
            data = json.loads(content)
            self.assertEqual(data["DB_PASS"], "netbox-pass")

    async def test_write_env_file_fails_if_exists(self):
        """Verify write_env_file fails if file already exists (security)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "existing.env")
            # Create existing file
            with open(path, "w") as f:
                f.write("existing content")

            with self.assertRaises(FileExistsError):
                await server.write_env_file_impl(
                    path=path,
                    secrets=[{"item": "netbox", "intent": "password", "key": "DB_PASS"}],
                    vault="AI",
                    client=self.fake_client,
                )


if __name__ == "__main__":
    unittest.main()
