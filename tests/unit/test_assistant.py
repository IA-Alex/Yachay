from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from motor import assistant
from motor.model import rules, store
from motor.storage.db import connect


class AssistantValidationTests(unittest.TestCase):
    schema = [{"name": "amount"}, {"name": "status"}]

    def test_registry_lists_only_existing_deterministic_rules(self):
        self.assertEqual([item["type"] for item in assistant.operations()],
                         list(rules.RULE_TYPES))

    def test_valid_proposal_supports_all_existing_rule_shapes(self):
        ruleset = [
            {"name": "rename_a", "type": "rename", "column": "amount",
             "params": {"to": "total"}},
            {"name": "cast_a", "type": "cast", "column": "amount",
             "params": {"to": "number"}},
            {"name": "map_a", "type": "map", "column": "status",
             "params": {"values": {"raw": "clean"}}},
            {"name": "domain_a", "type": "domain", "column": "status",
             "params": {"allowed": ["ok"]}},
            {"name": "filter_a", "type": "filter", "column": "status",
             "params": {"equals": "ok"}},
        ]
        actual = assistant.validate_proposal({"summary": "Listo", "rules": ruleset}, self.schema)
        self.assertEqual(actual["rules"], ruleset)

    def test_rejects_bad_instruction_and_overlong_instruction(self):
        with self.assertRaises(assistant.AssistantError):
            assistant.propose("  ", self.schema)
        with self.assertRaises(assistant.AssistantError):
            assistant.propose("x" * 2001, self.schema)

    def test_requires_key_and_rejects_oversized_schema(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(assistant.AssistantError, "credencial"):
                assistant.propose("limpia amount", self.schema)
        with patch.dict(os.environ, {"DEEPINFRA_API_KEY": "local-test"}):
            with self.assertRaisesRegex(assistant.AssistantError, "128 columnas"):
                assistant.propose("limpia amount", [{"name": str(i)} for i in range(129)])

    def test_rejects_malformed_proposal_envelope(self):
        for value in ([], {"summary": "ok", "rules": [], "extra": True},
                      {"summary": "", "rules": []},
                      {"summary": "x" * 501, "rules": []},
                      {"summary": "ok", "rules": "not-list"}):
            with self.subTest(value=value), self.assertRaises(assistant.AssistantError):
                assistant.validate_proposal(value, self.schema)

    def test_rejects_excessive_rule_count(self):
        ruleset = [self._rule(f"r{i}") for i in range(9)]
        with self.assertRaisesRegex(assistant.AssistantError, "0 y 8"):
            assistant.validate_proposal({"summary": "ok", "rules": ruleset}, self.schema)

    def test_rejects_invalid_shape_name_type_and_column(self):
        invalid = [
            {"summary": "ok", "rules": [{"name": "r", "type": "cast", "column": "amount"}]},
            {"summary": "ok", "rules": [self._rule("bad name")]},
            {"summary": "ok", "rules": [self._rule("r", type="arbitrary")]},
            {"summary": "ok", "rules": [self._rule("r", column="private")]},
            {"summary": "ok", "rules": [self._rule("r"), self._rule("r")]},
            {"summary": "ok", "rules": [self._rule("r", column=[])]},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(assistant.AssistantError):
                assistant.validate_proposal(value, self.schema)

    def test_rejects_each_invalid_rule_parameter_contract(self):
        invalid = [
            self._rule("r", type="rename", params={"to": ""}),
            self._rule("r", type="cast", params={"to": "integer"}),
            self._rule("r", type="map", params={"values": {"x": 4}}),
            self._rule("r", type="domain", params={"allowed": []}),
            self._rule("r", type="filter", params={"equals": "x", "other": 1}),
            self._rule("r", type="filter", params={"equals": ""}),
            self._rule("r", params=[]),
        ]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(assistant.AssistantError):
                assistant.validate_proposal({"summary": "ok", "rules": [item]}, self.schema)

    @staticmethod
    def _rule(name, *, type="cast", column="amount", params=None):
        return {"name": name, "type": type,
                "column": column, "params": {"to": "number"} if params is None else params}


class AssistantProviderTests(unittest.TestCase):
    class Response:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.payload

    def _response(self, content):
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    class Connection:
        def __init__(self, _host, **_kwargs):
            self.status = 200
            self.payload = b"{}"

        def request(self, _method, _path, body, **_kwargs):
            self.body = body

        def getresponse(self):
            return AssistantProviderTests.Response(self.payload)

        def close(self):
            pass

    def test_provider_receives_schema_but_no_source_rows(self):
        content = {"summary": "Convertir amount", "rules": [
            {"name": "amount_number", "type": "cast", "column": "amount",
             "params": {"to": "number"}},
        ]}
        connection = self.Connection("api.deepinfra.com")
        connection.payload = self._response(content)
        with patch.dict(os.environ, {"DEEPINFRA_API_KEY": "test-secret"}), \
                patch("motor.assistant._PinnedHTTPSConnection", return_value=connection):
            result = assistant.propose("convierte amount a número", [
                {"name": "amount", "inferred_type": "text", "rows": 12},
            ])
        sent = json.loads(connection.body)
        self.assertEqual(result["rules"], content["rules"])
        self.assertIn("amount", json.dumps(sent))
        self.assertNotIn("PRIVATE_ROW_VALUE", json.dumps(sent))

    def test_provider_error_and_invalid_response_are_sanitized(self):
        class BadResponse(self.Response):
            status = 502

        class BadConnection(self.Connection):
            def getresponse(self):
                return BadResponse(b"bad")

        with patch.dict(os.environ, {"DEEPINFRA_API_KEY": "secret"}), \
                patch("motor.assistant._PinnedHTTPSConnection", BadConnection):
            with self.assertRaisesRegex(assistant.AssistantError, "proveedor"):
                assistant.propose("ordena", self.schema)
        invalid = self.Connection("api.deepinfra.com")
        invalid.payload = b"{}"
        with patch.dict(os.environ, {"DEEPINFRA_API_KEY": "secret"}), \
                patch("motor.assistant._PinnedHTTPSConnection", return_value=invalid):
            with self.assertRaisesRegex(assistant.AssistantError, "inválida"):
                assistant.propose("ordena", self.schema)

    def test_provider_network_error_is_sanitized(self):
        with patch.dict(os.environ, {"DEEPINFRA_API_KEY": "secret"}), \
                patch("motor.assistant._PinnedHTTPSConnection", side_effect=OSError("private")):
            with self.assertRaisesRegex(assistant.AssistantError, "conectar") as error:
                assistant.propose("ordena", self.schema)
        self.assertNotIn("private", str(error.exception))

    def test_provider_response_size_is_capped(self):
        connection = self.Connection("api.deepinfra.com")
        connection.payload = b"x" * (64 * 1024 + 1)
        with patch.dict(os.environ, {"DEEPINFRA_API_KEY": "secret"}), \
                patch("motor.assistant._PinnedHTTPSConnection", return_value=connection):
            with self.assertRaisesRegex(assistant.AssistantError, "límite"):
                assistant.propose("ordena", self.schema)

    def test_provider_ip_file_is_validated_and_expires(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "provider-ips.json"
            good = {
                "host": "api.deepinfra.com",
                "addresses": ["8.8.8.8"],
                "refreshed_at": time.time(),
            }
            path.write_text(json.dumps(good), encoding="utf-8")
            with patch.dict(os.environ, {"YACHAY_DEEPINFRA_IPS_FILE": str(path)}):
                self.assertEqual(assistant._provider_addresses("api.deepinfra.com"), ["8.8.8.8"])
                for addresses, refreshed_at in (
                    (["127.0.0.1"], time.time()),
                    (["8.8.8.8"], time.time() - 181),
                ):
                    path.write_text(json.dumps({
                        "host": "api.deepinfra.com",
                        "addresses": addresses,
                        "refreshed_at": refreshed_at,
                    }), encoding="utf-8")
                    with self.assertRaises(assistant.AssistantError):
                        assistant._provider_addresses("api.deepinfra.com")

    @property
    def schema(self):
        return [{"name": "amount"}]


class ApplyProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.conn = connect(Path(self.temp.name) / "assistant.sqlite3")
        self.model_id = store.create(self.conn, name="Ventas", now=1)
        entity = store.add_entity(self.conn, self.model_id, name="Venta")
        store.add_attribute(self.conn, entity, name="importe", type="number", nullable=False)
        store.set_key(self.conn, entity, columns=["importe"])
        rules.add(self.conn, self.model_id, name="existing", type="cast",
                  column="importe", params={"to": "number"})
        self.conn.execute(
            "INSERT INTO joins (model_id, name, left_ref, right_ref, on_pairs, type, position) "
            "VALUES (?, 'j', 'a', 'b', '[]', 'inner', 0)", (self.model_id,)
        )

    def test_applies_rules_to_a_new_complete_version(self):
        proposal = [{"name": "importe_text", "type": "cast", "column": "importe",
                     "params": {"to": "text"}}]
        new_id, version = assistant.apply_as_new_version(
            self.conn, self.model_id, proposal, now=2
        )
        self.assertEqual(version, 2)
        self.assertEqual(store.get(self.conn, new_id).entities[0].key, ["importe"])
        self.assertEqual([rule.name for rule in rules.list_for(self.conn, new_id)],
                         ["existing", "importe_text"])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM joins WHERE model_id = ?", (new_id,)
        ).fetchone()[0], 1)
        self.assertEqual([rule.name for rule in rules.list_for(self.conn, self.model_id)],
                         ["existing"])

    def test_rejects_nonexistent_model_and_rolls_back(self):
        with self.assertRaises(assistant.AssistantError):
            assistant.apply_as_new_version(self.conn, 900, [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM models").fetchone()[0], 1)

    def test_rejects_empty_accepted_operation_list(self):
        with self.assertRaisesRegex(assistant.AssistantError, "No hay reglas"):
            assistant.apply_as_new_version(self.conn, self.model_id, [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM models").fetchone()[0], 1)

    def test_rejects_duplicate_rule_and_unknown_column_without_partial_clone(self):
        proposals = [
            [{"name": "existing", "type": "cast", "column": "importe",
              "params": {"to": "text"}}],
            [{"name": "new_rule", "type": "cast", "column": "other",
              "params": {"to": "text"}}],
            [{"name": "bad_type", "type": "noop", "column": "importe", "params": {}}],
        ]
        for proposal in proposals:
            with self.subTest(proposal=proposal), self.assertRaises(assistant.AssistantError):
                assistant.apply_as_new_version(self.conn, self.model_id, proposal)
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM models").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
